import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowsClockwise,
  MagnifyingGlass,
  Plus,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { Busy, Empty, ErrorNote, RivetIcon, type RivetIconName } from "./ui";
import type { RecordSummary } from "./record-types";
import "./order-feed.css";
import "./record-workspace.css";
import "./work-inbox.css";
import { useAccount } from "./Auth";
import { RecordIntake } from "./RecordIntake";

export type RecordInboxItem = {
  id: string;
  order_id: string;
  order_title: string;
  order_number: string;
  kind:
    | "reference_review"
    | "comment_review"
    | "response"
    | "task"
    | "proposal"
    | "run";
  title: string;
  detail: string;
  comment_id?: string;
  owner?: string;
  status: "needs_review" | "waiting" | "ready";
  updated_at: string;
  origin: "imported" | "automatic" | "ai" | "human";
  proposal_id?: string;
  run_id?: string;
  task_id?: string;
};
export function useRecordOrders() {
  return useQuery({
    queryKey: ["records"],
    queryFn: () => api<{ orders: RecordSummary[] }>("/records"),
    refetchInterval: 10000,
  });
}
export function useRecordInbox(enabled = true) {
  return useQuery({
    queryKey: ["record-inbox"],
    queryFn: () => api<{ items: RecordInboxItem[] }>("/records/inbox"),
    enabled,
    refetchInterval: 10000,
  });
}
const workIcon: Record<RecordInboxItem["kind"], RivetIconName> = {
  reference_review: "evidence",
  comment_review: "review",
  response: "comment",
  task: "inbox",
  proposal: "agent",
  run: "agent",
};
const originLabel: Record<RecordInboxItem["origin"], string> = {
  imported: "Imported",
  automatic: "Automatic",
  ai: "AI proposed",
  human: "Team task",
};
const statusLabel = {
  needs_review: "Needs review",
  waiting: "Waiting",
  ready: "Ready",
};

export function inboxDestination(item: RecordInboxItem) {
  const params = new URLSearchParams({ tab: "work" });
  if (item.comment_id) params.set("comment", item.comment_id);
  if (item.kind === "task") params.set("task", item.task_id || item.id);
  if (item.proposal_id || item.run_id || item.kind === "run")
    params.set("agent", "1");
  if (item.proposal_id) params.set("proposal", item.proposal_id);
  if (item.run_id) params.set("run", item.run_id);
  return `order/${item.order_id}?${params}`;
}

function WorkInbox({
  navigate,
  onNew,
}: {
  navigate: (to: string) => void;
  onNew: () => void;
}) {
  const query = useRecordInbox();
  const ordersQuery = useRecordOrders();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const items = query.data?.items ?? [];
  const visible = items.filter(
    (item) =>
      (filter === "all" || item.status === filter) &&
      `${item.title} ${item.detail} ${item.order_title} ${item.order_number} ${item.owner || ""}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const groups = [
    ...new Map(
      visible.map((item) => [
        item.order_id,
        {
          id: item.order_id,
          title: item.order_title,
          number: item.order_number,
        },
      ]),
    ).values(),
  ];
  return (
    <main className="page record-feed work-inbox">
      <div className="order-feed-heading">
        <div>
          <span className="eyebrow">Returned drawings & open decisions</span>
          <h1>Inbox</h1>
          <p>
            Review what changed, confirm the response, and move the order
            forward.
          </p>
        </div>
        <button className="secondary" onClick={onNew}>
          <Plus size={17} />
          Create order manually
        </button>
      </div>
      <RecordIntake
        navigate={navigate}
        orders={ordersQuery.data?.orders ?? []}
      />
      <section className="work-inbox-list" aria-label="Work across orders">
        <div className="order-feed-toolbar">
          <div
            className="order-feed-filters"
            role="group"
            aria-label="Filter work"
          >
            {["all", "needs_review", "waiting", "ready"].map((value) => (
              <button
                key={value}
                className={filter === value ? "selected" : ""}
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {value === "all"
                  ? "All work"
                  : statusLabel[value as keyof typeof statusLabel]}
                <span>
                  {value === "all"
                    ? items.length
                    : items.filter((item) => item.status === value).length}
                </span>
              </button>
            ))}
          </div>
          <label className="order-feed-search">
            <MagnifyingGlass size={17} />
            <input
              placeholder="Find work or an order…"
              aria-label="Search work inbox"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
        </div>
        {query.isError ? (
          <div className="inbox-state">
            <ErrorNote message={(query.error as Error).message} />
            <button
              className="text-button"
              onClick={() => void query.refetch()}
            >
              Try again <ArrowsClockwise size={15} />
            </button>
          </div>
        ) : query.isLoading ? (
          <div className="inbox-state">
            <Busy text="Loading work…" />
          </div>
        ) : visible.length ? (
          <div className="inbox-order-groups">
            {groups.map((group) => {
              const work = visible.filter((item) => item.order_id === group.id);
              const prepared = work.filter((item) => item.status === "ready");
              const review = work.filter(
                (item) => item.status === "needs_review",
              );
              return (
                <article className="inbox-order-group" key={group.id}>
                  <header>
                    <RivetIcon name="drawing" size={23} />
                    <div>
                      <small>{group.number}</small>
                      <button
                        className="inbox-order-title"
                        onClick={() => navigate(`order/${group.id}`)}
                      >
                        {group.title}
                      </button>
                      <p>
                        {review.length > 0 && (
                          <span>{review.length} need review</span>
                        )}
                        {prepared.length > 0 && (
                          <span className="inbox-prepared-count">
                            {prepared.length} prepared{" "}
                            {prepared.length === 1 ? "result" : "results"}
                          </span>
                        )}
                        {!review.length && !prepared.length && (
                          <span>
                            {work.length} outstanding{" "}
                            {work.length === 1 ? "item" : "items"}
                          </span>
                        )}
                      </p>
                    </div>
                    <button
                      className="secondary"
                      onClick={() => navigate(`order/${group.id}`)}
                    >
                      Review order
                      <ArrowRight size={16} />
                    </button>
                  </header>
                  {prepared.slice(0, 2).map((item) => (
                    <button
                      className="inbox-result-row"
                      key={item.id}
                      onClick={() => navigate(inboxDestination(item))}
                    >
                      <RivetIcon name="agent" size={18} />
                      <span>
                        <small>
                          {originLabel[item.origin]} · Ready for review
                        </small>
                        <strong>{item.title}</strong>
                      </span>
                      <ArrowRight size={16} />
                    </button>
                  ))}
                  <details className="inbox-work-details">
                    <summary>
                      View {work.length}{" "}
                      {work.length === 1 ? "work item" : "work items"}
                    </summary>
                    <div>
                      {work.map((item) => (
                        <button
                          key={`${item.kind}:${item.id}`}
                          className="inbox-detail-row"
                          onClick={() => navigate(inboxDestination(item))}
                        >
                          <RivetIcon name={workIcon[item.kind]} size={18} />
                          <span>
                            <strong>{item.title}</strong>
                            <small>{item.detail}</small>
                          </span>
                          <span className={`inbox-origin ${item.origin}`}>
                            {originLabel[item.origin]}
                          </span>
                          <ArrowRight size={15} />
                        </button>
                      ))}
                    </div>
                  </details>
                </article>
              );
            })}
          </div>
        ) : (
          <Empty
            icon={<RivetIcon name="inbox" size={30} />}
            title={
              items.length ? "No work matches this view" : "Your inbox is clear"
            }
          >
            {items.length
              ? "Try another filter or search."
              : "Open comments, assigned tasks, and prepared agent work appear here as your orders progress."}
          </Empty>
        )}
        <div className="order-feed-bottom">
          <span>
            {visible.length} {visible.length === 1 ? "work item" : "work items"}
          </span>
          <button
            className="text-button"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <ArrowsClockwise size={15} />
            Refresh
          </button>
        </div>
      </section>
    </main>
  );
}

export function RecordFeed({
  navigate,
  onNew,
  decisionsOnly = false,
}: {
  navigate: (to: string) => void;
  onNew: () => void;
  decisionsOnly?: boolean;
}) {
  const query = useRecordOrders();
  const account = useAccount();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const orders = query.data?.orders ?? [];
  const connectedDemo = orders.find(
    (order) => order.number === "DEMO-CONNECTED-01",
  );
  const drawingDemo = orders.find((order) => order.number === "PUBLIC-ALACHUA");
  const visible = orders.filter(
    (order) =>
      (filter !== "open" || order.counts.open > 0) &&
      (filter !== "approved" || order.approval) &&
      `${order.title} ${order.number} ${order.customer}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  if (decisionsOnly) return <WorkInbox navigate={navigate} onNew={onNew} />;
  return (
    <main className="page record-feed">
      <div className="order-feed-heading">
        <div>
          <span className="eyebrow">Your workspace</span>
          <h1>Orders</h1>
          <p>
            Each job has one comment log, with the source drawings and response
            history.
          </p>
        </div>
        <button className="secondary" onClick={onNew}>
          <Plus size={17} />
          Create order manually
        </button>
      </div>
      <RecordIntake navigate={navigate} orders={orders} />
      {account.demo && (
        <section className="record-demo-guide">
          <RivetIcon name="equipment" size={26} />
          <div>
            <h2>Explore a sample order</h2>
            <p>
              Public PDFs and a fictional review. Edits stay in this demo team.
            </p>
          </div>
          <button
            className="text-button"
            disabled={!connectedDemo && !drawingDemo}
            onClick={() => {
              const example = connectedDemo || drawingDemo;
              if (example) navigate(`order/${example.id}`);
            }}
          >
            Open sample <ArrowRight size={16} />
          </button>
        </section>
      )}
      <div className="record-overview">
        <span>
          <strong>{orders.length}</strong> orders
        </span>
        <span>
          <strong>
            {orders.reduce((n, order) => n + order.counts.open, 0)}
          </strong>{" "}
          open comments
        </span>
        <button className="text-button" onClick={() => navigate("decisions")}>
          <RivetIcon name="inbox" size={16} />
          Open inbox <ArrowRight size={14} />
        </button>
      </div>
      <section className="order-feed-list">
        <div className="order-feed-toolbar">
          <div
            className="order-feed-filters"
            role="group"
            aria-label="Filter orders"
          >
            {["all", "open", "approved"].map((value) => (
              <button
                key={value}
                className={filter === value ? "selected" : ""}
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {value === "all"
                  ? "All orders"
                  : value === "open"
                    ? "Open comments"
                    : "Approved records"}
              </button>
            ))}
          </div>
          <label className="order-feed-search">
            <MagnifyingGlass size={17} />
            <input
              placeholder="Search orders…"
              aria-label="Search order records"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
        </div>
        {query.isError ? (
          <div className="inbox-state">
            <ErrorNote message={(query.error as Error).message} />
          </div>
        ) : query.isLoading ? (
          <div className="inbox-state">
            <Busy text="Loading orders…" />
          </div>
        ) : visible.length ? (
          <div className="record-order-list">
            {visible.map((order) => (
              <button
                className="record-order-row"
                key={order.id}
                onClick={() => navigate(`order/${order.id}`)}
              >
                <RivetIcon name="equipment" size={24} />
                <span className="record-order-name">
                  <small>
                    {order.number}
                    {order.synthetic ? " · Sample order" : ""}
                  </small>
                  <strong>{order.title}</strong>
                  <span>{order.customer}</span>
                </span>
                <span className="record-order-count">
                  <strong>{order.counts.comments}</strong>
                  <small>comments</small>
                </span>
                <span className="record-order-state">
                  <span>
                    {order.counts.review
                      ? `${order.counts.review} need review`
                      : order.counts.open
                        ? `${order.counts.open} open`
                        : order.counts.comments
                          ? "Responses recorded"
                          : "Add documents"}
                  </span>
                  <small>
                    {order.approval
                      ? order.approval.label
                      : when(order.updated_at)}
                  </small>
                </span>
                <ArrowRight size={17} />
              </button>
            ))}
          </div>
        ) : (
          <Empty
            icon={<RivetIcon name="equipment" size={30} />}
            title="No orders in this view"
          >
            {orders.length
              ? "Try another search or filter."
              : "Create an order and import a submittal, marked-up PDF, or email."}
          </Empty>
        )}
        <div className="order-feed-bottom">
          <span>{visible.length} orders</span>
          <button
            className="text-button"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <ArrowsClockwise size={15} />
            Refresh
          </button>
        </div>
      </section>
    </main>
  );
}
