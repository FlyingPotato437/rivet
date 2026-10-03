import { useMemo, useState } from "react";
import {
  ArrowRight,
  ArrowsClockwise,
  Check,
  Checks,
  Circle,
  Files,
  GitBranch,
  MagnifyingGlass,
  Plus,
  ArrowUpRight,
  WarningCircle,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { api, when } from "./api";
import { Busy, Empty, ErrorNote, Modal } from "./ui";
import type { OrderSummary } from "./order-types";
import "./order-feed.css";

export function useOrders() {
  return useQuery({
    queryKey: ["orders"],
    queryFn: () => api<{ orders: OrderSummary[] }>("/orders"),
    refetchInterval: 4000,
  });
}
type Filter = "active" | "decisions" | "ready" | "released" | "all";
const statusLabel = (o: OrderSummary) =>
  o.status === "released"
    ? "Released"
    : o.status === "ready"
      ? "Ready for release"
      : o.counts.failing || o.counts.unknown
        ? "Needs review"
        : o.counts.decisions
          ? "Decisions pending"
          : o.counts.checks
            ? "In review"
            : "Awaiting documents";

export function OrderFeed({
  navigate,
  onNew,
  decisionsOnly = false,
}: {
  navigate: (to: string) => void;
  onNew: () => void;
  decisionsOnly?: boolean;
}) {
  const query = useOrders();
  const [filter, setFilter] = useState<Filter>(
    decisionsOnly ? "decisions" : "active",
  );
  const [search, setSearch] = useState("");
  const orders = query.data?.orders ?? [];
  const selected = decisionsOnly ? "decisions" : filter;
  const total = orders.reduce(
    (a, o) => ({
      decisions: a.decisions + o.counts.decisions,
      failing: a.failing + o.counts.failing + o.counts.unknown,
      ready: a.ready + (o.status === "ready" ? 1 : 0),
    }),
    { decisions: 0, failing: 0, ready: 0 },
  );
  const visible = useMemo(
    () =>
      orders
        .filter(
          (o) =>
            (selected === "all" ||
              (selected === "active" && o.status !== "released") ||
              (selected === "decisions" && o.counts.decisions > 0) ||
              (selected === "ready" && o.status === "ready") ||
              (selected === "released" && o.status === "released")) &&
            `${o.title} ${o.customer} ${o.number}`
              .toLowerCase()
              .includes(search.toLowerCase()),
        )
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [orders, selected, search],
  );
  return (
    <main className="page order-feed-page">
      <div className="order-feed-heading">
        <div>
          <span className="order-feed-eyebrow">
            <span />
            Equipment orders
          </span>
          <h1>{decisionsOnly ? "Decisions" : "Orders"}</h1>
          <p>
            {decisionsOnly
              ? "Orders with proposals awaiting review."
              : "Track revisions, open checks, and release status."}
          </p>
        </div>
        <button className="primary" onClick={onNew}>
          <Plus size={17} />
          New order
        </button>
      </div>
      <div className="order-feed-pulse" aria-label="Order health">
        <span>
          <GitBranch size={17} />
          <strong>
            {orders.filter((o) => o.status !== "released").length}
          </strong>{" "}
          active orders
        </span>
        <button
          onClick={() => {
            setFilter("decisions");
            if (!decisionsOnly) navigate("decisions");
          }}
        >
          <span className="pulse-dot violet" />
          <strong>{total.decisions}</strong> decisions for you
          <ArrowUpRight size={13} />
        </button>
        <span>
          <span className="pulse-dot amber" />
          <strong>{total.failing}</strong> checks need attention
        </span>
        <span>
          <Checks size={17} />
          <strong>{total.ready}</strong> ready for release
        </span>
      </div>
      <section className="order-feed-list" aria-label="Order feed">
        <div className="order-feed-toolbar">
          <div
            className="order-feed-filters"
            role="group"
            aria-label="Filter orders"
          >
            {(decisionsOnly
              ? [["decisions", "Needs a decision"]]
              : [
                  ["active", "Active"],
                  ["all", "All orders"],
                  ["ready", "Ready"],
                  ["released", "Released"],
                ]
            ).map(([value, label]) => (
              <button
                key={value}
                aria-pressed={selected === value}
                className={selected === value ? "selected" : ""}
                onClick={() => setFilter(value as Filter)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="order-feed-tools">
            <label className="order-feed-search">
              <MagnifyingGlass size={17} />
              <input
                placeholder="Find an order…"
                aria-label="Search orders"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <button
              className="icon-button"
              aria-label="Refresh orders"
              disabled={query.isFetching}
              onClick={() => void query.refetch()}
            >
              <ArrowsClockwise size={17} />
            </button>
          </div>
        </div>
        {query.isError ? (
          <div className="order-feed-empty">
            <ErrorNote message={(query.error as Error).message} />
            <button className="secondary" onClick={() => void query.refetch()}>
              Try again
            </button>
          </div>
        ) : query.isLoading ? (
          <div
            className="order-feed-skeleton"
            aria-label="Loading orders"
            role="status"
          >
            {[0, 1, 2].map((n) => (
              <div key={n}>
                <i />
                <span />
                <span />
              </div>
            ))}
          </div>
        ) : visible.length ? (
          <>
            <div className="order-feed-columns" aria-hidden="true">
              <span>Order / customer</span>
              <span>Current package</span>
              <span>Checks</span>
              <span>Next action</span>
            </div>
            {visible.map((order) => {
              const c = order.counts,
                attention = c.failing + c.unknown;
              return (
                <button
                  className="order-feed-row"
                  key={order.id}
                  onClick={() => navigate(`order/${order.id}`)}
                  aria-label={`Open order ${order.number}: ${order.title}`}
                >
                  <span className={`order-row-mark ${order.status}`}>
                    <Files size={23} weight="duotone" />
                  </span>
                  <span className="order-row-name">
                    <span className="order-row-number">
                      {order.number}
                      {order.synthetic && <small>Demo</small>}
                    </span>
                    <strong>{order.title}</strong>
                    <span>{order.customer}</span>
                  </span>
                  <span className="order-row-package">
                    <strong>
                      <GitBranch size={14} />
                      {order.revision_label}
                    </strong>
                    <span>{statusLabel(order)}</span>
                  </span>
                  <span className="order-row-checks">
                    <span className="order-check-bar" aria-hidden="true">
                      {c.checks ? (
                        <>
                          <i style={{ flex: c.passing }} />
                          <i className="waived" style={{ flex: c.waived }} />
                          <i className="failed" style={{ flex: c.failing }} />
                          <i className="unknown" style={{ flex: c.unknown }} />
                        </>
                      ) : (
                        <i className="unknown" />
                      )}
                    </span>
                    <strong>
                      {c.checks} checks <span>·</span>{" "}
                      <span
                        className={attention ? "needs-review" : "all-clear"}
                      >
                        {attention
                          ? `${attention} need review`
                          : c.checks
                            ? "All clear"
                            : "Add sources"}
                      </span>
                    </strong>
                  </span>
                  <span className="order-row-next">
                    {c.decisions ? (
                      <>
                        <span className="order-decision-count">
                          {c.decisions}
                        </span>
                        <span>
                          {c.decisions === 1 ? "decision" : "decisions"}
                        </span>
                      </>
                    ) : order.status === "released" ? (
                      <>
                        <Check size={16} />
                        <span>View release</span>
                      </>
                    ) : order.status === "ready" ? (
                      <>
                        <Checks size={17} />
                        <span>Review release</span>
                      </>
                    ) : c.tasks ? (
                      <>
                        <span className="order-decision-count">{c.tasks}</span>
                        <span>open tasks</span>
                      </>
                    ) : (
                      <>
                        <Circle size={13} />
                        <span>Open order</span>
                      </>
                    )}
                    <ArrowRight size={17} />
                  </span>
                </button>
              );
            })}
            <div className="order-feed-bottom">
              <span>
                {visible.length} {visible.length === 1 ? "order" : "orders"}
              </span>
              <span>Check results reflect the current source documents.</span>
            </div>
          </>
        ) : (
          <div className="order-feed-empty">
            <Empty
              icon={<Files size={34} />}
              title={
                search
                  ? "No matching orders"
                  : orders.length
                    ? "No orders match this filter"
                    : "No orders yet"
              }
              action={
                <button
                  className="primary"
                  onClick={
                    search
                      ? () => setSearch("")
                      : orders.length
                        ? () => {
                            setFilter("all");
                            navigate("orders");
                          }
                        : onNew
                  }
                >
                  {search
                    ? "Clear search"
                    : orders.length
                      ? "View all orders"
                      : "Create your first order"}
                  <ArrowRight size={15} />
                </button>
              }
            >
              {search
                ? "Search by order number, project, or customer."
                : "Bring the specification, purchase order, drawings, and review comments. Rivet connects the obligations, compares revisions, and prepares decisions for your review."}
            </Empty>
          </div>
        )}
      </section>
      <div className="order-feed-footnote">
        <span>
          <Check size={14} />
          Decisions and source references are recorded in order history.
        </span>
        <button className="text-button" onClick={() => navigate("guide")}>
          Workflow guide
          <ArrowUpRight size={14} />
        </button>
      </div>
    </main>
  );
}

export function NewOrder({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (order: OrderSummary) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal title="New order" eyebrow="Order details" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const data = new FormData(e.currentTarget);
          setBusy(true);
          setError("");
          try {
            const result = await api<{ order: OrderSummary }>("/orders", {
              title: data.get("title"),
              customer: data.get("customer"),
              number: data.get("number") || "",
              category: data.get("category") || "Custom equipment",
            });
            onCreated(result.order);
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="modal-intro">
          Enter the order details, then upload specifications, purchase orders,
          drawings, and review comments.
        </p>
        <label>
          Order name
          <input
            name="title"
            required
            minLength={2}
            maxLength={180}
            placeholder="Project / equipment package"
            autoFocus
          />
        </label>
        <div className="form-row">
          <label>
            Order number <span className="muted">optional</span>
            <input
              name="number"
              maxLength={60}
              placeholder="Your order reference"
            />
          </label>
          <label>
            Customer
            <input
              name="customer"
              required
              minLength={2}
              maxLength={180}
              placeholder="Customer or engineering team"
            />
          </label>
        </div>
        <label>
          Equipment category
          <input
            name="category"
            maxLength={100}
            defaultValue="Custom equipment"
            placeholder="e.g. Switchgear, control panels, machinery"
          />
        </label>
        <p className="record-note">
          Create a separate record for each custom order. Import comments from
          your own documents and emails.
        </p>
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy}>
            {busy ? (
              <Busy />
            ) : (
              <>
                Create order
                <ArrowRight size={16} />
              </>
            )}
          </button>
        </footer>
      </form>
    </Modal>
  );
}

export function OrderSearch({
  orders,
  onClose,
  navigate,
}: {
  orders: Pick<OrderSummary, "id" | "number" | "title" | "customer">[];
  onClose: () => void;
  navigate: (to: string) => void;
}) {
  const [query, setQuery] = useState("");
  const matches = orders.filter((o) =>
    `${o.title} ${o.customer} ${o.number}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <Modal title="Find an order" onClose={onClose}>
      <label className="global-search">
        <MagnifyingGlass size={20} />
        <input
          aria-label="Find an order"
          autoFocus
          placeholder="Order number, customer, or project…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      <div className="search-results">
        {matches.map((o) => (
          <button key={o.id} onClick={() => navigate(`order/${o.id}`)}>
            <Files size={20} />
            <span>
              {o.title}
              <small>
                {o.number} · {o.customer}
              </small>
            </span>
            <ArrowUpRight size={17} />
          </button>
        ))}
        {!matches.length && (
          <Empty title="No matching orders">Try another number or name.</Empty>
        )}
      </div>
    </Modal>
  );
}
