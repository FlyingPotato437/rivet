import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowsClockwise,
  MagnifyingGlass,
  Plus,
  UploadSimple,
  ArrowLeft,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { workOverview, workInStatus } from "./review-queue";
import { Busy, Empty, ErrorNote, RivetIcon, type RivetIconName } from "./ui";
import type { RecordSummary } from "./record-types";
import "./order-feed.css";
import "./record-workspace.css";
import "./work-inbox.css";
import "./review-companion.css";
import { useAccount } from "./Auth";
import { RecordIntake, IntakeConfirmation, type Intake } from "./RecordIntake";

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
    | "prepared_reply"
    | "lifecycle"
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
  run_status?: string;
  base_version?: number;
  current_version?: number;
  run_origin?: string;
  needs_refresh?: boolean;
  comment_ids?: string[];
  superseded_by?: string;
  review_required?: boolean;
  prepared_automatically?: boolean;
  prepared_outputs?: number;
  lifecycle?: import("./review-queue").WorkOverviewItem["lifecycle"];
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
  prepared_reply: "comment",
  lifecycle: "drawing",
};
const originLabel: Record<RecordInboxItem["origin"], string> = {
  imported: "Imported",
  automatic: "Automatic",
  ai: "AI proposed",
  human: "Team task",
};
const statusLabel = {
  needs_review: "Needs you",
  waiting: "Rivet working",
  ready: "Results",
};

type MailboxSummary = {
  items: {
    id: string;
    enabled: boolean;
    status: string;
    last_reconciled_at?: string | null;
  }[];
};

export function inboxDestination(item: RecordInboxItem) {
  if (item.kind === "lifecycle") return `order/${item.order_id}`;
  const params = new URLSearchParams({ tab: "work" });
  if (item.comment_id) params.set("comment", item.comment_id);
  if (item.kind === "task") params.set("task", item.task_id || item.id);
  if (item.proposal_id || item.run_id || item.kind === "run")
    params.set("agent", "1");
  if (item.proposal_id) params.set("proposal", item.proposal_id);
  if (item.run_id) params.set("run", item.run_id);
  return `order/${item.order_id}?${params}`;
}

function workTitle(item: RecordInboxItem) {
  if (item.kind !== "run") return item.title;
  if (item.run_status === "queued") return "Order review queued";
  if (item.run_status === "running" || item.status === "waiting")
    return "Rivet is reviewing the order";
  if (item.run_status === "waiting_for_input")
    return "Rivet needs more information";
  if (item.run_status === "waiting_for_review")
    return "Rivet’s work is prepared";
  if (item.run_status === "failed") return "Review an interrupted task";
  if (item.run_status === "paused") return "Resume a paused task";
  return "Review Rivet’s task";
}

function WorkInbox({
  navigate,
  onNew,
}: {
  navigate: (to: string) => void;
  onNew: () => void;
}) {
  const qc = useQueryClient();
  const query = useRecordInbox();
  const ordersQuery = useRecordOrders();
  const intakeQuery = useQuery({
    queryKey: ["record-intake", "pending"],
    queryFn: () =>
      api<{ items: Intake[] }>("/records/intake?status=needs_confirmation"),
    refetchInterval: 10000,
  });
  const mailboxes = useQuery({
    queryKey: ["review-mailbox-status"],
    queryFn: () => api<MailboxSummary>("/mailboxes"),
    refetchInterval: 30000,
  });
  const [search, setSearch] = useState("");
  const [filter, setFilter] =
    useState<keyof typeof statusLabel>("needs_review");
  const groups = workOverview(query.data?.items ?? []);
  const matching =
    intakeQuery.data?.items.filter(
      (item) => item.status === "needs_confirmation",
    ) ?? [];
  const text = search.trim().toLowerCase();
  const visible = groups.filter(
    (group) =>
      workInStatus(group, filter) &&
      group.items.some((item) =>
        `${item.title} ${item.detail} ${item.order_title} ${item.order_number} ${item.owner || ""}`
          .toLowerCase()
          .includes(text),
      ),
  );
  const unmatched =
    filter === "needs_review"
      ? matching.filter((item) =>
          `${item.filename} ${item.email?.subject || ""} ${item.email?.from || ""} ${item.reason}`
            .toLowerCase()
            .includes(text),
        )
      : [];
  const count = (status: keyof typeof statusLabel) =>
    groups.filter((group) => workInStatus(group, status)).length +
    (status === "needs_review" ? matching.length : 0);
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const incoming = params.has("intake") || params.get("add") === "1";
  const activeMailboxes =
    mailboxes.data?.items.filter((item) => item.enabled) ?? [];
  return (
    <main className="page record-feed review-companion">
      <header className="review-heading">
        <div>
          {incoming && (
            <button
              className="text-button review-back"
              onClick={() => navigate("review")}
            >
              <ArrowLeft size={16} /> Back to review
            </button>
          )}
          <h1>
            {incoming
              ? params.has("intake")
                ? "Confirm the order"
                : "Add a returned file"
              : "Needs you"}
          </h1>
          <p>
            {incoming
              ? params.has("intake")
                ? "Review the message, then confirm where it belongs."
                : "Keep the email and its attachments together when you have them."
              : "Rivet prepares the work in Outlook. Open the record here when you need more detail."}
          </p>
        </div>
        {!incoming && (
          <button
            className="secondary"
            onClick={() => navigate("review?add=1")}
          >
            <UploadSimple size={17} /> Add files
          </button>
        )}
      </header>
      {incoming ? (
        <RecordIntake
          navigate={navigate}
          orders={ordersQuery.data?.orders ?? []}
        />
      ) : (
        <>
          <div className="review-mailbox-line">
            <span className="review-mailbox-indicator" aria-hidden="true" />
            <span>
              {mailboxes.isError
                ? "Mailbox status unavailable"
                : mailboxes.isPending
                  ? "Checking mailbox status…"
                  : activeMailboxes.length
                    ? `${activeMailboxes.length} mailbox${activeMailboxes.length === 1 ? "" : "es"} configured · Check sync coverage in settings`
                    : "Outlook is not connected yet"}
            </span>
            <button
              className="text-button"
              onClick={() => navigate("settings")}
            >
              {activeMailboxes.length ? "Connection status" : "Set up Outlook"}
            </button>
          </div>
          <section
            className="review-queue"
            aria-label="Order work requiring attention"
          >
            <div className="review-toolbar">
              <nav className="review-buckets" aria-label="Review work status">
                {(Object.keys(statusLabel) as (keyof typeof statusLabel)[]).map(
                  (status) => (
                    <button
                      key={status}
                      aria-pressed={filter === status}
                      className={filter === status ? "selected" : ""}
                      onClick={() => setFilter(status)}
                    >
                      {statusLabel[status]}
                      <span>{count(status)}</span>
                    </button>
                  ),
                )}
              </nav>
              <label className="review-search">
                <MagnifyingGlass size={17} />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Find work…"
                  aria-label="Search review work"
                />
              </label>
            </div>
            {(query.isError || intakeQuery.isError) && (
              <div className="review-state">
                <ErrorNote message="Some review work could not load. Refresh to check the complete queue." />
                <button
                  className="text-button"
                  onClick={() => {
                    void query.refetch();
                    void intakeQuery.refetch();
                  }}
                >
                  Try again
                </button>
              </div>
            )}
            {query.isPending || intakeQuery.isPending ? (
              <div className="review-state">
                <Busy text="Loading review work…" />
              </div>
            ) : (
              <>
                {visible.length > 0 || unmatched.length > 0 ? (
                  <div className="review-rows">
                    {unmatched.map((item) => (
                      <IntakeConfirmation
                        key={item.intake_id}
                        item={item}
                        orders={ordersQuery.data?.orders ?? []}
                        confirmed={(next) => {
                          qc.setQueryData(
                            ["record-intake", next.intake_id],
                            next,
                          );
                          void qc.invalidateQueries({
                            queryKey: ["record-intake"],
                          });
                          void qc.invalidateQueries({
                            queryKey: ["record-inbox"],
                          });
                          void qc.invalidateQueries({ queryKey: ["records"] });
                        }}
                      />
                    ))}
                    {visible.map((group) => {
                      const item =
                        filter === "ready"
                          ? group.items.find((part) => part.prepared_outputs) ||
                            group.lead
                          : group.lead;
                      const destination = group.recordedOnly
                        ? `order/${item.order_id}`
                        : inboxDestination(item);
                      const preparedCount = group.items.reduce(
                        (total, part) => total + (part.prepared_outputs || 0),
                        0,
                      );
                      const updatedAt = group.items.reduce(
                        (latest, part) =>
                          part.updated_at > latest ? part.updated_at : latest,
                        item.updated_at,
                      );
                      const title = preparedCount
                        ? `${preparedCount} ${preparedCount === 1 ? "draft ready" : "drafts ready"}${group.status === "needs_review" ? " · Open point" : ""}`
                        : group.recordedOnly
                          ? `${group.items.length} ${group.items.length === 1 ? "comment recorded" : "comments recorded"}`
                          : group.status === "needs_review"
                            ? item.kind === "run" &&
                              item.run_status === "failed"
                              ? "Rivet couldn’t finish"
                              : item.kind === "run" &&
                                  item.run_status === "waiting_for_input"
                                ? "One detail is needed"
                                : "A decision is needed"
                            : group.status === "waiting"
                              ? "Rivet is on it"
                              : "Prepared for you";
                      return (
                        <div
                          className="review-order-group"
                          key={`${item.order_id}:${group.status}:${group.recordedOnly}`}
                        >
                          <button
                            className={`review-row ${group.status}`}
                            onClick={() => navigate(destination)}
                          >
                            <span className="review-row-symbol">
                              <RivetIcon name={workIcon[item.kind]} size={20} />
                            </span>
                            <span className="review-row-main">
                              <strong>{title}</strong>
                              <span>
                                {group.recordedOnly
                                  ? "Sources and comment history are available when you need them."
                                  : filter === "ready"
                                    ? group.status === "needs_review"
                                      ? "The drafts are saved. Open points remain on this order."
                                      : "The work is saved. Open it when you want the details."
                                    : item.detail}
                              </span>
                              <small>
                                {preparedCount
                                  ? group.items.some(
                                      (part) =>
                                        part.prepared_outputs &&
                                        part.origin === "ai",
                                    )
                                    ? "AI-prepared drafts"
                                    : "Rule-based drafts"
                                  : originLabel[item.origin]}
                                {item.owner ? ` · ${item.owner}` : ""}
                              </small>
                            </span>
                            <span className="review-row-order">
                              {item.order_number || item.order_title}
                              <small>{when(updatedAt)}</small>
                            </span>
                            <span className="review-row-action">
                              {group.recordedOnly
                                ? "View record"
                                : filter === "ready"
                                  ? "View result"
                                  : filter === "waiting"
                                    ? "View progress"
                                    : "Review"}
                              <ArrowRight size={16} />
                            </span>
                          </button>
                          {!group.recordedOnly && group.items.length > 1 && (
                            <details className="review-group-items">
                              <summary>Supporting work and open points</summary>
                              {group.items.map((part) => (
                                <button
                                  key={part.id}
                                  onClick={() =>
                                    navigate(inboxDestination(part))
                                  }
                                >
                                  <strong>{workTitle(part)}</strong>
                                  <span>{part.detail}</span>
                                </button>
                              ))}
                            </details>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="review-empty">
                    <RivetIcon
                      name={filter === "waiting" ? "agent" : "review"}
                      size={30}
                    />
                    <h2>
                      {text
                        ? "No matching work"
                        : filter === "waiting"
                          ? "No tasks running"
                          : filter === "ready"
                            ? "No prepared work yet"
                            : "Nothing needs your decision"}
                    </h2>
                    <p>
                      {text
                        ? "Try a different order number or search phrase."
                        : filter === "waiting"
                          ? "Only tasks Rivet has actually queued or started appear here."
                          : filter === "ready"
                            ? "Prepared work and recorded sources appear here. Open an order to see its reply."
                            : "Rivet will ask only when an order match, missing information, or a decision needs your input."}
                    </p>
                  </div>
                )}
              </>
            )}
            <footer className="review-footer">
              <span>
                {visible.length + unmatched.length}{" "}
                {visible.length + unmatched.length === 1 ? "item" : "items"}
              </span>
              <button
                className="text-button"
                disabled={query.isFetching || intakeQuery.isFetching}
                onClick={() => {
                  void query.refetch();
                  void intakeQuery.refetch();
                }}
              >
                <ArrowsClockwise size={15} /> Refresh
              </button>
            </footer>
          </section>
          <footer className="review-secondary">
            <span>
              Most work starts in your email. Use this view when a decision
              needs more context.
            </span>
            <button className="text-button" onClick={onNew}>
              Create an order manually
            </button>
          </footer>
        </>
      )}
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
  const [addingFiles, setAddingFiles] = useState(false);
  const orders = query.data?.orders ?? [];
  const connectedDemo = orders.find(
    (order) => order.number === "DEMO-CONNECTED-01",
  );
  const drawingDemo = orders.find((order) => order.number === "PUBLIC-ALACHUA");
  const visible = orders.filter((order) =>
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
          <p>Documents, correspondence, and prepared work for each job.</p>
        </div>
        <div className="orders-header-actions">
          <button
            className="text-button"
            onClick={() => setAddingFiles(!addingFiles)}
          >
            <UploadSimple size={17} />{" "}
            {addingFiles ? "Close upload" : "Add files"}
          </button>
        </div>
      </div>
      {addingFiles && <RecordIntake navigate={navigate} orders={orders} />}
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
      <section className="order-feed-list">
        <div className="order-feed-toolbar">
          <span className="orders-list-label">All orders</span>
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
                    {order.counts.open
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
          <button className="text-button" onClick={onNew}>
            Create an order manually
          </button>
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
