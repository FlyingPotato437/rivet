import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle,
  DownloadSimple,
  UploadSimple,
  X,
} from "@phosphor-icons/react";
import { api, apiFetch } from "./api";
import { Busy, ErrorNote, RivetIcon } from "./ui";
import type { RecordSummary } from "./record-types";
import { TeamInboxControl } from "./Integrations";

type Intake = {
  intake_id: string;
  filename: string;
  status: "matched" | "created" | "needs_confirmation";
  order_id: string | null;
  detected: {
    job_number?: string;
    revision?: string;
    job_numbers?: string[];
    revisions?: string[];
  };
  candidates: {
    id: string;
    number: string;
    title: string;
    reasons?: string[];
  }[];
  reason: string;
  revision_label?: string;
  duplicate?: boolean;
};

export function RecordIntake({
  navigate,
  orders,
}: {
  navigate: (to: string) => void;
  orders: RecordSummary[];
}) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Intake | null>(null);
  const query = useQuery({
    queryKey: ["record-intake"],
    queryFn: () => api<{ items: Intake[] }>("/records/intake"),
    refetchInterval: 10000,
  });
  const waiting =
    query.data?.items.filter((item) => item.status === "needs_confirmation") ??
    [];
  const candidates =
    result?.status === "needs_confirmation" &&
    !waiting.some((item) => item.intake_id === result.intake_id)
      ? [result, ...waiting]
      : waiting;
  async function upload(file?: File) {
    if (!file || busy) return;
    if (!/\.(pdf|eml|txt)$/i.test(file.name)) {
      setError("Choose a PDF, saved EML email, or TXT file.");
      return;
    }
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const form = new FormData();
      form.set("file", file);
      const next = await api<Intake>("/records/intake", form);
      setResult(next);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["record-intake"] }),
        qc.invalidateQueries({ queryKey: ["records"] }),
        qc.invalidateQueries({ queryKey: ["record-inbox"] }),
        next.order_id
          ? qc.invalidateQueries({ queryKey: ["record", next.order_id] })
          : Promise.resolve(),
      ]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }
  return (
    <section className="record-intake" aria-label="Returned drawing intake">
      <div
        className={`record-intake-drop${dragging ? " is-dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (event.dataTransfer.files.length !== 1) {
            setError(
              "Add one returned package at a time. Use a saved EML email to keep its attachments together.",
            );
            return;
          }
          void upload(event.dataTransfer.files[0]);
        }}
      >
        <RivetIcon name="inbox" size={29} />
        <div>
          <h2>
            {busy
              ? "Reading the order details…"
              : "A returned drawing starts here"}
          </h2>
          <p>
            {busy
              ? "The file is being matched to an order. Unclear details come back to you."
              : "Drop a PDF or saved email. Rivet checks the job and revision before building the log."}
          </p>
        </div>
        <input
          ref={input}
          type="file"
          tabIndex={-1}
          accept=".pdf,.eml,.txt"
          aria-label="Upload returned drawing or saved email"
          onChange={(event) => void upload(event.target.files?.[0])}
          disabled={busy}
        />
        <button
          className="primary"
          disabled={busy}
          onClick={() => input.current?.click()}
        >
          {busy ? (
            <Busy text="Reading…" />
          ) : (
            <>
              <UploadSimple size={17} />
              Add returned drawing
            </>
          )}
        </button>
      </div>
      <TeamInboxControl navigate={navigate} />
      {error && <ErrorNote message={error} />}
      {query.isError && (
        <div className="record-intake-error">
          <ErrorNote message="Could not load files waiting for an order match." />
          <button className="text-button" onClick={() => void query.refetch()}>
            Try again
          </button>
        </div>
      )}
      {result?.order_id && (
        <div className="record-intake-success" role="status">
          <CheckCircle size={20} />
          <div>
            <strong>
              {result.duplicate
                ? "This file is already in the order"
                : result.status === "created"
                  ? "Order created from the document"
                  : "Document added to the order"}
            </strong>
            <span>
              {result.filename} · {result.reason}
            </span>
          </div>
          <button
            className="text-button"
            onClick={() => navigate(`order/${result.order_id}`)}
          >
            Open comment log
            <ArrowRight size={16} />
          </button>
          <button
            className="icon-button"
            aria-label="Dismiss upload result"
            onClick={() => setResult(null)}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {candidates.length > 0 && (
        <div className="record-intake-matches">
          <div className="record-intake-matches-heading">
            <strong>
              {candidates.length}{" "}
              {candidates.length === 1 ? "file needs" : "files need"} an order
              confirmed
            </strong>
            <span>Nothing is attached until you confirm.</span>
          </div>
          {candidates.map((item) => (
            <IntakeConfirmation
              key={item.intake_id}
              item={item}
              orders={orders}
              confirmed={(next) => {
                setResult(next);
                void qc.invalidateQueries({ queryKey: ["record-intake"] });
                void qc.invalidateQueries({ queryKey: ["records"] });
                void qc.invalidateQueries({ queryKey: ["record-inbox"] });
                if (next.order_id)
                  void qc.invalidateQueries({ queryKey: ["record", next.order_id] });
              }}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function IntakeConfirmation({
  item,
  orders,
  confirmed,
}: {
  item: Intake;
  orders: RecordSummary[];
  confirmed: (result: Intake) => void;
}) {
  const [orderId, setOrderId] = useState(
    item.candidates.length === 1 ? item.candidates[0].id : "",
  );
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ordered = [
    ...new Map(
      [...item.candidates, ...orders].map((order) => [order.id, order]),
    ).values(),
  ];
  async function original() {
    setError("");
    try {
      const response = await apiFetch(
        `/api/records/intake/${item.intake_id}/source`,
      );
      if (!response.ok)
        throw new Error("Could not open the original file. Try again.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = item.filename;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <article className="intake-match">
      <button
        className="intake-match-summary"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <RivetIcon name="drawing" size={21} />
        <span>
          <strong>{item.filename}</strong>
          <small>{item.reason}</small>
        </span>
        <span className="intake-match-action">
          Confirm order
          <ArrowRight size={15} />
        </span>
      </button>
      {expanded && (
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError("");
            try {
              const values = Object.fromEntries(
                new FormData(event.currentTarget),
              );
              confirmed(
                await api<Intake>(`/records/intake/${item.intake_id}/confirm`, {
                  ...values,
                  order_id: orderId === "new" ? undefined : orderId,
                }),
              );
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="intake-match-fields">
            <label>
              Which order?
              <select
                value={orderId}
                onChange={(event) => setOrderId(event.target.value)}
                required
              >
                <option value="">Choose an order</option>
                {ordered.map((order) => (
                  <option key={order.id} value={order.id}>
                    {order.number} · {order.title}
                  </option>
                ))}
                <option value="new">Create an order for a new job</option>
              </select>
            </label>
            <label>
              Returned revision
              <input
                name="revision_label"
                defaultValue={
                  item.detected.revision || item.revision_label || ""
                }
                required
                maxLength={40}
                placeholder="e.g. C, or Unspecified"
              />
            </label>
            {orderId === "new" && (
              <>
                <label>
                  Job number
                  <input
                    name="job_number"
                    required
                    maxLength={80}
                    defaultValue={item.detected.job_number || ""}
                    placeholder="Job number from the document"
                  />
                </label>
                <label>
                  Order title
                  <input
                    name="title"
                    required
                    maxLength={180}
                    placeholder="Name used by your team"
                  />
                </label>
                <label>
                  Customer
                  <input
                    name="customer"
                    maxLength={180}
                    placeholder="Optional"
                  />
                </label>
              </>
            )}
          </div>
          {error && <ErrorNote message={error} />}
          <footer>
            <button
              type="button"
              className="text-button"
              onClick={() => void original()}
            >
              <DownloadSimple size={15} />
              Download original
            </button>
            <button className="primary" disabled={busy || !orderId}>
              {busy ? "Adding…" : "Confirm and add"}
              <ArrowRight size={16} />
            </button>
          </footer>
        </form>
      )}
    </article>
  );
}
