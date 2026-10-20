import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { RecordView } from "./record-types";

export type EditableCustomerNote = {
  id: string;
  key: string;
  version: number;
  title: string;
  text: string;
  kind: string;
  topics: string[];
  category: string;
  applicability: string;
  applicability_note: string;
  source_order_id: string;
  conflicts_with: string[];
  expires_at?: string | null;
  provenance: { evidence: { source_id?: string; document_id?: string }[] };
};
export function CustomerNoteEditor({
  customerId,
  orders,
  existing,
  onSaved,
  onCancel,
}: {
  customerId: string;
  orders: { order_id: string; number: string; title: string }[];
  existing?: EditableCustomerNote;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [order, setOrder] = useState(existing?.source_order_id || "");
  const [source, setSource] = useState(
    existing?.provenance.evidence[0]?.source_id || "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const record = useQuery({
    queryKey: ["record", order],
    queryFn: () => api<RecordView>(`/orders/${order}/record`),
    enabled: !!order,
  });
  const selected = record.data?.sources.find((s) => s.id === source);
  return (
    <form
      className="customer-form"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        const form = new FormData(e.currentTarget);
        try {
          await api(`/company-context/customers/${customerId}/notes`, {
            key: existing?.key || `request-${crypto.randomUUID()}`,
            expected_version: existing?.version || 0,
            title: form.get("title"),
            text: form.get("text"),
            kind: existing?.kind || "preference",
            topics: String(form.get("topics"))
              .split(",")
              .map((t) => t.trim())
              .filter(Boolean),
            category: record.data?.order.category || "",
            applicability: form.get("applicability"),
            applicability_note: form.get("condition"),
            source_order_id: order,
            source_ids: [source],
            provenance_note:
              "PM proposed a reusable note from the selected historical source; separate approval required.",
            conflicts_with: existing?.conflicts_with || [],
            expires_at: existing?.expires_at || null,
          });
          onSaved();
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <h2>
        {existing
          ? "Propose a reusable preference"
          : "Add a request from a past order"}
      </h2>
      <p className="customer-guidance">
        Select the original source. Your team reviews this candidate before
        Rivet uses it on another job.
      </p>
      {error && <p role="alert">{error}</p>}
      <label>
        Source order
        <select
          required
          value={order}
          onChange={(e) => {
            setOrder(e.target.value);
            setSource("");
          }}
        >
          <option value="" disabled>
            Choose a linked order
          </option>
          {orders.map((o) => (
            <option key={o.order_id} value={o.order_id}>
              {o.number} · {o.title}
            </option>
          ))}
        </select>
      </label>
      {record.isError && (
        <p role="alert">
          Could not load source documents. Try selecting the order again.
        </p>
      )}
      <label>
        Original passage
        <select
          required
          value={source}
          onChange={(e) => setSource(e.target.value)}
          disabled={!record.data}
        >
          <option value="" disabled>
            Choose an extracted passage
          </option>
          {record.data?.sources.map((s) => (
            <option key={s.id} value={s.id}>
              {record.data.documents.find((d) => d.id === s.document_id)?.name}{" "}
              · {s.text.slice(0, 100)}
            </option>
          ))}
        </select>
      </label>
      {selected && (
        <blockquote className="customer-source-excerpt">
          {selected.text.slice(0, 1500)}
        </blockquote>
      )}
      <label>
        Title
        <input
          name="title"
          defaultValue={existing?.title}
          required
          minLength={2}
          maxLength={180}
          placeholder="Show cable entry on the general arrangement"
        />
      </label>
      <label>
        Customer request
        <textarea
          name="text"
          defaultValue={existing?.text}
          required
          minLength={3}
          maxLength={6000}
          rows={4}
        />
      </label>
      <div className="customer-form-row">
        <label>
          How it applies
          <select
            name="applicability"
            defaultValue={
              existing ? "standing_preference" : "historical_reference"
            }
          >
            <option value="historical_reference">
              Reference for this past order
            </option>
            <option value="standing_preference">
              Preference to check on future orders
            </option>
          </select>
        </label>
        <label>
          Topics, separated by commas
          <input
            name="topics"
            defaultValue={existing?.topics.join(", ")}
            placeholder="cable entry, drawing notes"
            required
          />
        </label>
      </div>
      <label>
        When should the team use this?
        <textarea
          name="condition"
          defaultValue={existing?.applicability_note}
          required
          minLength={8}
          maxLength={2000}
          rows={2}
          placeholder="For this customer’s indoor switchgear. Confirm against the current job requirements."
        />
      </label>
      <div className="customer-editor-actions">
        <button
          type="submit"
          className="primary"
          disabled={busy || !source || !record.data}
        >
          {busy ? "Saving…" : "Save for approval"}
        </button>
        <button type="button" className="text-button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
