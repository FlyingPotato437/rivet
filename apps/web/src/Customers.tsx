import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Plus,
  MagnifyingGlass,
  Buildings,
  ArrowClockwise,
} from "@phosphor-icons/react";
import { api } from "./api";
import { useAccount } from "./Auth";
import { useRecordOrders } from "./RecordFeed";
import { Busy, ErrorNote } from "./ui";
import "./customers.css";
import {
  CustomerNoteEditor,
  type EditableCustomerNote,
} from "./CustomerNoteEditor";

type Customer = {
  id: string;
  key: string;
  name: string;
  description: string;
  order_count: number;
  candidate_count: number;
  approved_count: number;
  count_note?: string;
};
type Note = EditableCustomerNote & {
  id: string;
  title: string;
  text: string;
  version: number;
  status: string;
  evidence_current: boolean;
  applicability: string;
  applicability_note: string;
  source_order_id: string;
  conflicts_with: string[];
  provenance: { evidence: { source_id?: string; document_id?: string }[] };
};
type Detail = {
  customer: Customer;
  orders: {
    order_id: string;
    number: string;
    title: string;
    binding_version: number;
  }[];
  notes: Note[];
  has_more_notes?: boolean;
};
export default function Customers({
  navigate,
  customerId,
}: {
  navigate: (to: string) => void;
  customerId?: string;
}) {
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const query = useQuery({
    queryKey: ["customers"],
    queryFn: () =>
      api<{ customers: Customer[]; has_more?: boolean }>(
        "/company-context/customers",
      ),
  });
  if (customerId)
    return (
      <CustomerDetail key={customerId} id={customerId} navigate={navigate} />
    );
  const customers =
    query.data?.customers.filter((c) =>
      `${c.name} ${c.key}`.toLowerCase().includes(search.toLowerCase()),
    ) ?? [];
  return (
    <main className="page customer-page">
      <header className="review-heading">
        <div>
          <h1>Customers</h1>
          <p>Reviewed context Rivet can use across a customer’s orders.</p>
        </div>
        <button className="secondary" onClick={() => setAdding(!adding)}>
          <Plus size={17} />
          {adding ? "Cancel" : "Add customer"}
        </button>
      </header>
      {error && <ErrorNote message={error} />}
      {adding && (
        <form
          className="customer-form"
          onSubmit={async (e) => {
            e.preventDefault();
            const form = new FormData(e.currentTarget);
            setBusy(true);
            setError("");
            try {
              const saved = await api<Customer>("/company-context/customers", {
                key: form.get("key"),
                name: form.get("name"),
                description: form.get("description"),
              });
              await query.refetch();
              navigate(`customer/${saved.id}`);
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="customer-form-row">
            <label>
              Customer name
              <input name="name" required maxLength={180} />
            </label>
            <label>
              Customer code
              <input
                name="key"
                required
                pattern="[a-z0-9][a-z0-9_-]*"
                placeholder="customer-code"
                maxLength={100}
              />
            </label>
          </div>
          <label>
            About this customer <span>Optional</span>
            <textarea name="description" rows={2} maxLength={2000} />
          </label>
          <button className="primary" disabled={busy}>
            {busy ? "Saving…" : "Create customer"}
          </button>
        </form>
      )}
      {query.data?.has_more && (
        <p className="customer-guidance">
          Showing the first set of customer records. Additional records are not
          displayed in this view.
        </p>
      )}
      <label className="customer-search">
        <MagnifyingGlass size={18} />
        <input
          aria-label="Search customers"
          placeholder="Find a customer"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </label>
      {query.isError ? (
        <ErrorNote message={(query.error as Error).message} />
      ) : query.isPending ? (
        <Busy text="Loading customers…" />
      ) : customers.length ? (
        <div className="customer-list">
          {customers.map((c) => (
            <button
              key={c.id}
              className="customer-list-row"
              onClick={() => navigate(`customer/${c.id}`)}
            >
              <Buildings size={23} />
              <span>
                <strong>{c.name}</strong>
                <small>{c.key}</small>
              </span>
              <span className="customer-count">
                {c.order_count} orders
                <small>
                  {c.approved_count} approval records
                  {c.candidate_count ? ` · ${c.candidate_count} to review` : ""}
                </small>
              </span>
              <ArrowRight size={17} />
            </button>
          ))}
        </div>
      ) : (
        <div className="customer-empty">
          <Buildings size={30} />
          <h2>
            {search
              ? "No matching customers"
              : "Keep customer context together"}
          </h2>
          <p>
            Add a customer and link their orders. Rivet can prepare reusable
            notes from your team’s reviewed responses; an administrator approves
            what carries forward.
          </p>
        </div>
      )}
    </main>
  );
}
function CustomerDetail({
  id,
  navigate,
}: {
  id: string;
  navigate: (to: string) => void;
}) {
  const account = useAccount();
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["customer", id],
    queryFn: () => api<Detail>(`/company-context/customers/${id}`),
  });
  const orders = useRecordOrders();
  const [tab, setTab] = useState<"knowledge" | "orders">("knowledge");
  const [linking, setLinking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reviewId, setReviewId] = useState("");
  const [editing, setEditing] = useState<Note | "new" | null>(null);
  async function act(
    path: string,
    body: unknown,
    message: string,
    method = "POST",
  ) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{ partial?: boolean }>(path, body, method);
      await query.refetch();
      void qc.invalidateQueries({ queryKey: ["customers"] });
      void qc.invalidateQueries({ queryKey: ["customer-brief"] });
      setNotice(
        result?.partial
          ? "Some source context could not be checked. Review the available notes; run the check again before assuming the customer record is complete."
          : message,
      );
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  if (query.isError)
    return (
      <main className="page customer-page">
        <ErrorNote message={(query.error as Error).message} />
        <button className="text-button" onClick={() => navigate("customers")}>
          Back to customers
        </button>
      </main>
    );
  if (!query.data)
    return (
      <main className="page customer-page">
        <Busy text="Loading customer context…" />
      </main>
    );
  const d = query.data;
  const selected = d.notes.find((n) => n.id === reviewId);
  return (
    <main className="page customer-page">
      <button
        className="text-button customer-back"
        onClick={() => navigate("customers")}
      >
        <ArrowLeft size={16} /> Customers
      </button>
      <header className="review-heading">
        <div>
          <h1>{d.customer.name}</h1>
          <span className="customer-code">{d.customer.key}</span>
          <p>
            {d.customer.description ||
              "Source-backed context for this customer’s orders."}
          </p>
        </div>
        <button
          className="secondary"
          disabled={busy}
          onClick={async () => {
            if (
              await act(
                `/company-context/customers/${id}/notes/refresh`,
                {},
                "Reviewed responses checked. New notes, if found, are waiting for approval.",
              )
            )
              setTab("knowledge");
          }}
        >
          <ArrowClockwise size={17} /> Check for new context
        </button>
      </header>
      {error && <ErrorNote message={error} />}{" "}
      {notice && (
        <p className="customer-notice" role="status">
          {notice}
        </p>
      )}
      <nav className="customer-tabs" aria-label="Customer views">
        <button
          className={tab === "knowledge" ? "active" : ""}
          onClick={() => setTab("knowledge")}
        >
          Customer context{" "}
          <span>{d.notes.filter((n) => n.status !== "revoked").length}</span>
        </button>
        <button
          className={tab === "orders" ? "active" : ""}
          onClick={() => setTab("orders")}
        >
          Orders <span>{d.orders.length}</span>
        </button>
      </nav>
      {tab === "knowledge" ? (
        <>
          <div className="customer-order-tools">
            <p>
              Carry forward the requests your customer has already explained.
            </p>
            <button
              className="secondary"
              disabled={!d.orders.length}
              onClick={() => setEditing("new")}
            >
              Add a past request
            </button>
          </div>
          {editing && (
            <CustomerNoteEditor
              key={editing === "new" ? "new" : editing.id}
              customerId={id}
              orders={d.orders}
              existing={editing === "new" ? undefined : editing}
              onCancel={() => setEditing(null)}
              onSaved={() => {
                setEditing(null);
                void query.refetch();
                void qc.invalidateQueries({ queryKey: ["customer-brief"] });
                setNotice(
                  "Candidate saved. Review its source and approve it before reuse.",
                );
              }}
            />
          )}
          {d.customer.count_note && (
            <p className="customer-guidance">{d.customer.count_note}</p>
          )}
          <p className="customer-guidance">
            Only approved, current notes can inform a reply. Historical
            decisions remain references; they do not approve a new order.
          </p>
          {d.has_more_notes && (
            <p className="customer-guidance">
              Additional notes exist beyond this view. The records shown here
              are not the full customer history.
            </p>
          )}
          {d.notes.length ? (
            <div className="customer-knowledge">
              {d.notes.map((note) => (
                <article className="customer-note" key={note.id}>
                  <div className="customer-note-copy">
                    <div className="customer-note-status">
                      <span>
                        {!note.evidence_current
                          ? "Source changed"
                          : note.status === "candidate"
                            ? "Needs approval"
                            : note.status === "approved"
                              ? "Approved context"
                              : "Revoked"}
                      </span>
                      <span>
                        {note.applicability === "standing_preference"
                          ? "Standing preference"
                          : "Historical reference"}
                      </span>
                    </div>
                    <h2>{note.title}</h2>
                    <p>{note.text}</p>
                    <div className="customer-note-links">
                      {note.provenance.evidence
                        .filter((e) => e.source_id || e.document_id)
                        .slice(0, 3)
                        .map((e, i) => (
                          <button
                            key={i}
                            className="text-button"
                            onClick={() =>
                              navigate(
                                `order/${note.source_order_id}?tab=work&source=${encodeURIComponent(e.source_id || e.document_id || "")}`,
                              )
                            }
                          >
                            View source {i + 1}
                          </button>
                        ))}
                      <button
                        className="text-button"
                        onClick={() =>
                          setReviewId(reviewId === note.id ? "" : note.id)
                        }
                      >
                        {reviewId === note.id
                          ? "Close details"
                          : "Review details"}
                      </button>
                      {note.status === "approved" &&
                        note.applicability !== "standing_preference" && (
                          <button
                            className="text-button"
                            onClick={() => setEditing(note)}
                          >
                            Propose as recurring preference
                          </button>
                        )}
                    </div>
                    {selected?.id === note.id && (
                      <div className="customer-note-review">
                        <p>{note.applicability_note}</p>
                        {note.conflicts_with.length > 0 && (
                          <p className="customer-warning">
                            This note records a conflict with other customer
                            context. Resolve the conflict before relying on it.
                          </p>
                        )}
                        {account.role === "org:admin" &&
                          note.status !== "revoked" && (
                            <form
                              onSubmit={async (e) => {
                                e.preventDefault();
                                const form = new FormData(e.currentTarget);
                                await act(
                                  `/company-context/customer-notes/${note.id}/${note.status === "approved" ? "revoke" : "approve"}`,
                                  {
                                    expected_version: note.version,
                                    reason: form.get("reason"),
                                  },
                                  note.status === "approved"
                                    ? "Note revoked. It will no longer be used in replies."
                                    : "Note approved for customer context.",
                                );
                              }}
                            >
                              <label>
                                Review note
                                <input
                                  name="reason"
                                  required
                                  minLength={8}
                                  maxLength={1000}
                                  placeholder="Why this context is applicable"
                                />
                              </label>
                              <button
                                className="secondary"
                                disabled={
                                  busy ||
                                  (!note.evidence_current &&
                                    note.status !== "approved")
                                }
                              >
                                {note.status === "approved"
                                  ? "Revoke note"
                                  : "Approve context"}
                              </button>
                            </form>
                          )}
                        {account.role !== "org:admin" && (
                          <p>
                            An administrator reviews whether a note may be
                            reused.
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="customer-empty">
              <h2>No customer notes yet</h2>
              <p>
                Link completed orders, then check for context from reviewed
                comments and responses. Nothing is inferred from a matching
                company name.
              </p>
              <button
                className="text-button"
                onClick={() => {
                  setTab("orders");
                  setLinking(true);
                }}
              >
                Link an order <ArrowRight size={16} />
              </button>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="customer-order-tools">
            <p>These orders explicitly belong to {d.customer.name}.</p>
            <button className="secondary" onClick={() => setLinking(!linking)}>
              <Plus size={16} />
              {linking ? "Cancel" : "Link order"}
            </button>
          </div>
          {linking && (
            <form
              className="customer-form"
              onSubmit={async (e) => {
                e.preventDefault();
                const form = new FormData(e.currentTarget);
                const orderId = String(form.get("order"));
                setBusy(true);
                setError("");
                try {
                  const binding = await api<{
                    version: number;
                    customer: Customer | null;
                  }>(`/company-context/orders/${orderId}/customer`);
                  if (binding.customer && binding.customer.id !== id)
                    throw new Error(
                      `This order is already linked to ${binding.customer.name}. Review that customer before moving it.`,
                    );
                  if (
                    await act(
                      `/company-context/orders/${orderId}/customer`,
                      {
                        customer_id: id,
                        expected_version: binding.version,
                        reason: form.get("reason"),
                      },
                      "Order linked to this customer.",
                      "PUT",
                    )
                  )
                    setLinking(false);
                } catch (err) {
                  setError((err as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <label>
                Order
                <select name="order" required defaultValue="">
                  <option value="" disabled>
                    Choose an order
                  </option>
                  {orders.data?.orders
                    .filter(
                      (o) => !d.orders.some((link) => link.order_id === o.id),
                    )
                    .map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.number} · {o.title}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Connection note
                <input
                  name="reason"
                  required
                  minLength={8}
                  maxLength={1000}
                  placeholder="How you confirmed this is the customer’s order"
                />
              </label>
              <button className="primary" disabled={busy}>
                Link order
              </button>
            </form>
          )}
          <div className="customer-list">
            {d.orders.map((o) => (
              <button
                className="customer-list-row"
                key={o.order_id}
                onClick={() => navigate(`order/${o.order_id}`)}
              >
                <span>
                  <strong>{o.title}</strong>
                  <small>{o.number}</small>
                </span>
                <ArrowRight size={17} />
              </button>
            ))}
          </div>
          {!d.orders.length && !linking && (
            <div className="customer-empty">
              <h2>No linked orders</h2>
              <p>
                Link an order to give Rivet a confirmed customer relationship.
              </p>
            </div>
          )}
        </>
      )}
    </main>
  );
}
