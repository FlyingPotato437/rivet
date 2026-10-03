import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowsClockwise,
  ChatText,
  MagnifyingGlass,
  Plus,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { Busy, Empty, ErrorNote } from "./ui";
import type { RecordSummary } from "./record-types";
import "./order-feed.css";
import "./record-workspace.css";
import { useAccount } from "./Auth";
export function useRecordOrders() {
  return useQuery({
    queryKey: ["records"],
    queryFn: () => api<{ orders: RecordSummary[] }>("/records"),
    refetchInterval: 5000,
  });
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
  const visible = orders.filter(
    (o) =>
      (!decisionsOnly || o.counts.review > 0) &&
      (filter !== "open" || o.counts.open > 0) &&
      (filter !== "approved" || o.approval) &&
      `${o.title} ${o.number} ${o.customer}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <main className="page record-feed">
      <div className="order-feed-heading">
        <div>
          <span className="eyebrow">Custom equipment orders</span>
          <h1>{decisionsOnly ? "Needs review" : "Orders"}</h1>
          <p>
            {decisionsOnly
              ? "Comments that need their details or drawing location confirmed."
              : "Comments, responses, and approved changes for each order."}
          </p>
        </div>
        <button className="primary" onClick={onNew}>
          <Plus size={17} />
          New order
        </button>
      </div>
      {account.demo && (
        <section className="record-demo-guide">
          <div>
            <span className="eyebrow">Public-document demo</span>
            <h2>Start with the switchgear review</h2>
            <p>
              Open a comment, check the highlighted PDF source, and save your
              review. Then record a response or change and export the log. All
              edits stay in this practice team.
            </p>
          </div>
          <button
            className="secondary"
            disabled={!orders.some((o) => o.number === "PUBLIC-ALACHUA")}
            onClick={() => {
              const order = orders.find((o) => o.number === "PUBLIC-ALACHUA");
              if (order) navigate(`order/${order.id}`);
            }}
          >
            Open drawing review <ArrowRight size={16} />
          </button>
        </section>
      )}
      <div className="record-overview">
        <span>
          <strong>{orders.length}</strong> orders
        </span>
        <span>
          <strong>{orders.reduce((n, o) => n + o.counts.open, 0)}</strong> open
          comments
        </span>
        <span>
          <strong>{orders.reduce((n, o) => n + o.counts.review, 0)}</strong>{" "}
          need review
        </span>
      </div>
      <section className="order-feed-list">
        <div className="order-feed-toolbar">
          <div
            className="order-feed-filters"
            role="group"
            aria-label="Filter orders"
          >
            {["all", "open", "approved"].map((f) => (
              <button
                key={f}
                className={filter === f ? "selected" : ""}
                aria-pressed={filter === f}
                onClick={() => setFilter(f)}
              >
                {f === "all"
                  ? "All orders"
                  : f === "open"
                    ? "Open comments"
                    : "Approved records"}
              </button>
            ))}
          </div>
          <label className="order-feed-search">
            <MagnifyingGlass size={16} />
            <input
              placeholder="Search orders…"
              aria-label="Search order records"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
        {query.isError ? (
          <ErrorNote message={(query.error as Error).message} />
        ) : query.isLoading ? (
          <Busy text="Loading orders…" />
        ) : visible.length ? (
          <div className="record-order-list">
            {visible.map((o) => (
              <button
                className="record-order-row"
                key={o.id}
                onClick={() => navigate(`order/${o.id}`)}
              >
                <ChatText size={24} weight="duotone" />
                <span className="record-order-name">
                  <small>
                    {o.number}
                    {o.synthetic ? " · Sample order" : ""}
                  </small>
                  <strong>{o.title}</strong>
                  <span>{o.customer}</span>
                </span>
                <span className="record-order-count">
                  <strong>{o.counts.comments}</strong>
                  <small>comments</small>
                </span>
                <span className="record-order-state">
                  <span>
                    {o.counts.review
                      ? `${o.counts.review} need review`
                      : o.counts.open
                        ? `${o.counts.open} open`
                        : o.counts.comments
                          ? "Responses recorded"
                          : "Add documents"}
                  </span>
                  <small>
                    {o.approval ? o.approval.label : when(o.updated_at)}
                  </small>
                </span>
                <ArrowRight size={17} />
              </button>
            ))}
          </div>
        ) : (
          <Empty title="No orders in this view">
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
            <ArrowsClockwise size={14} />
            Refresh
          </button>
        </div>
      </section>
    </main>
  );
}
