import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowUpRight } from "@phosphor-icons/react";
import { api } from "./api";
import { Busy, ErrorNote } from "./ui";
import type { RecordView } from "./record-types";

type Card = {
  id: string;
  order_id: string;
  number: string;
  title: string;
  gate: string;
  source_ids: string[];
  status: string;
  result: { decision?: string };
};
export function ApprovalEvidence({
  data,
  sourceIds,
  open,
  loading,
  error,
}: {
  data?: RecordView;
  sourceIds: string[];
  open: (id: string) => void;
  loading?: boolean;
  error?: boolean;
}) {
  if (loading) return <Busy text="Loading supporting evidence…" />;
  if (error)
    return (
      <ErrorNote message="Evidence could not load. Try again before deciding." />
    );
  return (
    <div className="approval-evidence">
      {data?.sources
        .filter((s) => sourceIds.includes(s.id))
        .map((s) => (
          <article key={s.id}>
            <p>{s.text}</p>
            <button className="text-button" onClick={() => open(s.id)}>
              Open original <ArrowUpRight size={14} />
            </button>
          </article>
        ))}
    </div>
  );
}
export function ApprovalReview({
  id,
  navigate,
}: {
  id: string;
  navigate: (to: string) => void;
}) {
  const q = useQuery({
    queryKey: ["approval-card", id],
    queryFn: () => api<Card>(`/approval-cards/${id}`),
  });
  const evidence = useQuery({
    queryKey: ["approval-evidence", q.data?.order_id],
    enabled: !!q.data,
    queryFn: () => api<RecordView>(`/orders/${q.data!.order_id}/record`),
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  async function decide(decision: "approve" | "reject") {
    setError("");
    setBusy(true);
    try {
      await api(`/approval-cards/${id}/decision`, { decision });
      await qc.invalidateQueries({ queryKey: ["approval-card", id] });
      await qc.invalidateQueries({ queryKey: ["project-work"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (q.isPending)
    return (
      <main className="page activity-page">
        <Busy text="Opening decision…" />
      </main>
    );
  if (!q.data)
    return (
      <main className="page activity-page">
        <ErrorNote message="This decision is unavailable. Open the current order." />
      </main>
    );
  const c = q.data;
  return (
    <main className="page activity-page approval-review">
      <button
        className="text-button"
        onClick={() => navigate(`order/${c.order_id}`)}
      >
        <ArrowLeft size={16} />
        Back to order
      </button>
      <header>
        <p>
          {c.number} ·{" "}
          {c.gate === "money"
            ? "Price"
            : c.gate === "engineering"
              ? "Drawing"
              : c.gate === "scope"
                ? "Scope"
                : "Customer response"}{" "}
          decision
        </p>
        <h1>{c.title}</h1>
      </header>
      {error && <ErrorNote message={error} />}
      <section>
        <h2>Evidence</h2>
        {evidence.data?.sources
          .filter((s) => c.source_ids.includes(s.id))
          .map((s) => (
            <article key={s.id}>
              <p>{s.text}</p>
              <button
                className="text-button"
                onClick={() => navigate(`order/${c.order_id}?source=${s.id}`)}
              >
                Open original <ArrowUpRight size={14} />
              </button>
            </article>
          ))}
      </section>
      {c.status === "decided" ? (
        <p role="status">Decision recorded: {c.result.decision}.</p>
      ) : (
        <footer>
          <button disabled={busy} onClick={() => decide("approve")}>
            Approve this work
          </button>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => decide("reject")}
          >
            Reject
          </button>
          <button
            className="text-button"
            onClick={() =>
              navigate(`order/${c.order_id}?view=advanced&tab=sharing`)
            }
          >
            Edit in order
          </button>
        </footer>
      )}
      <p className="project-sync-note">
        Your company’s named approval authority and the current evidence are
        checked before this decision is recorded.
      </p>
    </main>
  );
}
