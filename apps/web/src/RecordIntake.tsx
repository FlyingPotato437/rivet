import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowLeft,
  CheckCircle,
  DownloadSimple,
  UploadSimple,
  X,
  CaretDown,
  EnvelopeSimple,
  Paperclip,
  ArrowUpRight,
} from "@phosphor-icons/react";
import { api, apiFetch } from "./api";
import { Busy, ErrorNote, RivetIcon } from "./ui";
import type { RecordSummary } from "./record-types";
import { TeamInboxControl } from "./Integrations";

type IntakePreview = {
  kind: "email" | "file";
  subject?: string;
  from?: string;
  to?: string;
  date?: string;
  received_at?: string;
  body?: string;
  truncated?: boolean;
  origin?: string;
  outlook_url?: string;
  error?: string;
  attachments?: { index: number; name: string; size: number }[];
};

export type Intake = {
  email?: { subject?: string; from?: string; date?: string };
  intake_id: string;
  filename: string;
  status:
    | "matched"
    | "created"
    | "needs_confirmation"
    | "set_aside"
    | "not_order_related";
  order_id: string | null;
  detected: {
    job_number?: string;
    revision?: string;
    job_numbers?: string[];
    revisions?: string[];
    po_numbers?: string[];
    drawing_numbers?: string[];
  };
  candidates: {
    id: string;
    number: string;
    title: string;
    reasons?: string[];
    identifier_conflict?: boolean;
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
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const changed = () => setHash(window.location.hash);
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  const params = new URLSearchParams(hash.split("?")[1] || "");
  const focusedId = params.has("intake") ? params.get("intake")! : null;
  const query = useQuery({
    queryKey: ["record-intake", "pending"],
    queryFn: () =>
      api<{ items: Intake[] }>("/records/intake?status=needs_confirmation"),
    enabled: focusedId === null,
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
  function confirmed(next: Intake) {
    setResult(next);
    qc.setQueryData(["record-intake", next.intake_id], next);
    void qc.invalidateQueries({ queryKey: ["record-intake"] });
    void qc.invalidateQueries({ queryKey: ["records"] });
    void qc.invalidateQueries({ queryKey: ["record-inbox"] });
    if (next.order_id)
      void qc.invalidateQueries({ queryKey: ["record", next.order_id] });
  }
  if (focusedId !== null)
    return (
      <FocusedIntake
        key={focusedId}
        id={focusedId}
        orders={orders}
        navigate={navigate}
        confirmed={confirmed}
      />
    );
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
              confirmed={confirmed}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function FocusedIntake({
  id,
  orders,
  navigate,
  confirmed,
}: {
  id: string;
  orders: RecordSummary[];
  navigate: (to: string) => void;
  confirmed: (result: Intake) => void;
}) {
  const panel = useRef<HTMLElement>(null);
  const focused = useRef(false);
  const valid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id);
  const query = useQuery({
    queryKey: ["record-intake", id],
    enabled: valid,
    queryFn: async () => {
      const response = await apiFetch(
        `/api/records/intake/${encodeURIComponent(id)}`,
      );
      if (response.status === 404)
        throw new Error(
          "This incoming file is not available in this workspace. Check that you have opened the correct team.",
        );
      if (!response.ok)
        throw new Error("Could not open the incoming file. Try again.");
      return response.json() as Promise<Intake>;
    },
    retry: false,
    refetchInterval: (state) =>
      state.state.data?.status === "needs_confirmation" ? 10000 : false,
  });
  const item = query.data;
  const ready = !valid || query.isError || !!item;
  useEffect(() => {
    // Focus once per linked file, never on polling or while the PM is typing.
    if (!ready || focused.current) return;
    const frame = requestAnimationFrame(() => {
      panel.current?.scrollIntoView({ block: "start", behavior: "auto" });
      panel.current?.focus({ preventScroll: true });
      focused.current = true;
    });
    return () => cancelAnimationFrame(frame);
  }, [ready]);
  return (
    <section
      className="record-intake"
      aria-label="Linked incoming file"
      tabIndex={-1}
      ref={panel}
      style={{ scrollMarginTop: 96 }}
    >
      {!valid ? (
        <ErrorNote message="This incoming-file link is incomplete or invalid. Open all incoming files to find the document." />
      ) : query.isError ? (
        <div className="record-intake-error">
          <ErrorNote message={query.error.message} />
          <button className="text-button" onClick={() => void query.refetch()}>
            Try again
          </button>
        </div>
      ) : !item ? (
        <Busy text="Opening the incoming file…" />
      ) : item.status === "needs_confirmation" ? (
        <div className="record-intake-matches">
          <IntakeConfirmation
            key={item.intake_id}
            item={item}
            orders={orders}
            confirmed={confirmed}
            initiallyExpanded
          />
        </div>
      ) : ["set_aside", "not_order_related"].includes(item.status) ? (
        <SetAsideIntake item={item} confirmed={confirmed} />
      ) : item.order_id ? (
        <div className="record-intake-success" role="status">
          <CheckCircle size={20} />
          <div>
            <strong>This file has been added to its order</strong>
            <span>
              {item.filename} · {item.reason}
            </span>
          </div>
          <button
            className="text-button"
            onClick={() => navigate(`order/${item.order_id}`)}
          >
            Open comment log <ArrowRight size={16} />
          </button>
        </div>
      ) : (
        <ErrorNote message="This file no longer has an order to open. Return to incoming files to check its status." />
      )}
    </section>
  );
}

function SetAsideIntake({
  item,
  confirmed,
}: {
  item: Intake;
  confirmed: (result: Intake) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <div className="intake-set-aside" role="status">
      <h2>
        {item.status === "not_order_related"
          ? "No order context found"
          : "Set aside from order review"}
      </h2>
      <p>{item.email?.subject || "Incoming message"}</p>
      <p>
        This message is outside the order review queue. The original is retained
        in mailbox history; you can return it to review if needed.
      </p>
      {error && <ErrorNote message={error} />}
      <button
        className="secondary"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            confirmed(
              await api<Intake>(
                `/records/intake/${item.intake_id}/restore`,
                {},
              ),
            );
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Restoring…" : "Return to review"}
      </button>
    </div>
  );
}

export function IntakeConfirmation({
  item,
  orders,
  confirmed,
  initiallyExpanded = false,
}: {
  item: Intake;
  orders: RecordSummary[];
  confirmed: (result: Intake) => void;
  initiallyExpanded?: boolean;
}) {
  // A drawing number alone can suggest an unrelated job. Require an explicit
  // selection here; confidently matched jobs never enter this form.
  const [orderId, setOrderId] = useState("");
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const [assigning, setAssigning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const previewQuery = useQuery({
    queryKey: ["record-intake-preview", item.intake_id],
    queryFn: () =>
      api<IntakePreview>(`/records/intake/${item.intake_id}/preview`),
    enabled: expanded,
    staleTime: 60000,
  });
  const preview = previewQuery.data;
  const isEmail = preview?.kind === "email" || /\.eml$/i.test(item.filename);
  const title =
    preview?.subject ||
    item.email?.subject ||
    (isEmail ? "Email without a subject" : item.filename);
  const detected = item.detected;
  const matchReason =
    item.candidates.length > 1
      ? "More than one order matches this message. Choose the correct job below."
      : (detected.job_numbers?.length || 0) > 1
        ? "This message mentions more than one job number. Confirm which order this return belongs to."
        : !detected.job_number && !item.candidates.length
          ? "Rivet couldn’t identify an order from the message. Check the message and attachments, then choose the job."
          : (detected.revisions?.length || 0) > 1
            ? "More than one revision was found. Confirm the returned revision before adding it."
            : !detected.revision
              ? "No revision is stated. Rivet will record it as Unspecified."
              : item.reason;
  const suggestion =
    item.candidates.length === 1 &&
    !item.candidates[0].identifier_conflict &&
    (detected.job_numbers?.length || 0) <= 1 &&
    (detected.revisions?.length || 0) <= 1
      ? item.candidates[0]
      : null;
  async function decide(yes: boolean) {
    if (yes && !suggestion) {
      setExpanded(true);
      setAssigning(true);
      return;
    }
    setBusy(true);
    setError("");
    try {
      confirmed(
        await api<Intake>(
          `/records/intake/${item.intake_id}/${yes ? "confirm" : "set-aside"}`,
          yes
            ? {
                order_id: suggestion!.id,
                revision_label: detected.revision || "Unspecified",
              }
            : {},
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const ordered = [
    ...new Map(
      [...item.candidates, ...orders].map((order) => [order.id, order]),
    ).values(),
  ];
  async function download(path: string, name: string) {
    setError("");
    try {
      const response = await apiFetch(
        `/api/records/intake/${item.intake_id}/${path}`,
      );
      if (!response.ok)
        throw new Error("Could not download the file. Try again.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError((err as Error).message);
    }
  }
  const heading = (
    <>
      {isEmail ? (
        <EnvelopeSimple size={22} />
      ) : (
        <RivetIcon name="drawing" size={22} />
      )}
      <span>
        <strong>{title}</strong>
        <small>
          {preview?.origin === "Outlook"
            ? "Captured from Outlook"
            : isEmail
              ? "Incoming email"
              : "Incoming document"}
        </small>
      </span>
    </>
  );
  return (
    <article
      className={`intake-match${initiallyExpanded ? " intake-match-focused" : ""}`}
    >
      <header className="intake-triage-header">
        <button
          className="intake-match-summary"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
        >
          {heading}
        </button>
        <div className="intake-triage-decision">
          <span>
            {suggestion ? `Add to ${suggestion.number}?` : "Order-related?"}
          </span>
          <div>
            <button
              className="secondary"
              disabled={busy}
              title="Set aside this message. You can restore it later."
              onClick={() => void decide(false)}
            >
              No
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() => void decide(true)}
            >
              {busy ? "Saving…" : "Yes"}
            </button>
          </div>
          {suggestion && (
            <small>
              {detected.revision
                ? `Rev ${detected.revision}`
                : "Revision not stated · saved as Unspecified"}
            </small>
          )}
        </div>
      </header>
      {error && !assigning && <ErrorNote message={error} />}
      {expanded && (
        <div
          className={`intake-confirmation-layout${assigning ? "" : " intake-preview-only"}`}
        >
          <section className="intake-message" aria-label="Incoming message">
            {previewQuery.isPending ? (
              <Busy text="Loading message…" />
            ) : previewQuery.isError ? (
              <div>
                <ErrorNote message="Could not load the email preview." />
                <button
                  className="text-button"
                  onClick={() => void previewQuery.refetch()}
                >
                  Try again
                </button>
              </div>
            ) : preview?.error ? (
              <ErrorNote message={preview.error} />
            ) : (
              <>
                {isEmail && (
                  <dl className="intake-message-meta">
                    <div>
                      <dt>From</dt>
                      <dd>{preview?.from || "Sender not recorded"}</dd>
                    </div>
                    {preview?.to && (
                      <div>
                        <dt>To</dt>
                        <dd>{preview.to}</dd>
                      </div>
                    )}
                    {(preview?.date || preview?.received_at) && (
                      <div>
                        <dt>Received</dt>
                        <dd>{preview.date || preview.received_at}</dd>
                      </div>
                    )}
                  </dl>
                )}
                <div
                  className="intake-message-body"
                  tabIndex={0}
                  aria-label="Message contents"
                >
                  {preview?.body ||
                    (isEmail
                      ? "No readable message text. Check the attachments or download the original email."
                      : "Open the original document to check its order and revision.")}
                </div>
                {preview?.truncated && (
                  <p className="intake-message-note">
                    Showing the first 30,000 characters. Download the original
                    for the complete message.
                  </p>
                )}
                {!!preview?.attachments?.length && (
                  <div className="intake-attachments">
                    <h3>Attachments · {preview.attachments.length}</h3>
                    {preview.attachments.map((file) => (
                      <button
                        type="button"
                        key={file.index}
                        onClick={() =>
                          void download(`attachments/${file.index}`, file.name)
                        }
                      >
                        <Paperclip size={17} />
                        <span>
                          {file.name}
                          <small>
                            {Math.max(1, Math.round(file.size / 1024))} KB
                          </small>
                        </span>
                        <DownloadSimple size={16} />
                      </button>
                    ))}
                  </div>
                )}
                {preview?.outlook_url && (
                  <a
                    className="text-button intake-outlook-link"
                    href={preview.outlook_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open in Outlook <ArrowUpRight size={15} />
                  </a>
                )}
              </>
            )}
          </section>
          {assigning && (
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
                    await api<Intake>(
                      `/records/intake/${item.intake_id}/confirm`,
                      {
                        ...values,
                        order_id: orderId === "new" ? undefined : orderId,
                      },
                    ),
                  );
                } catch (err) {
                  setError((err as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <h3>Match to an order</h3>
              <p className="intake-match-explanation">{matchReason}</p>
              {!!item.candidates.length && (
                <p className="intake-match-hints">
                  Possible matches:{" "}
                  {item.candidates
                    .map(
                      (c) =>
                        `${c.number}${c.reasons?.length ? ` (${c.reasons.join(", ")})` : ""}`,
                    )
                    .join("; ")}
                </p>
              )}
              {!!(
                detected.job_numbers?.length ||
                detected.revisions?.length ||
                detected.drawing_numbers?.length
              ) && (
                <dl className="intake-detected">
                  {!!detected.job_numbers?.length && (
                    <div>
                      <dt>Job mentioned</dt>
                      <dd>{detected.job_numbers.join(", ")}</dd>
                    </div>
                  )}
                  {!!detected.revisions?.length && (
                    <div>
                      <dt>Revision mentioned</dt>
                      <dd>{detected.revisions.join(", ")}</dd>
                    </div>
                  )}
                  {!!detected.drawing_numbers?.length && (
                    <div>
                      <dt>Drawing mentioned</dt>
                      <dd>{detected.drawing_numbers.join(", ")}</dd>
                    </div>
                  )}
                </dl>
              )}
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
                    required={(detected.revisions?.length || 0) > 1}
                    maxLength={40}
                    placeholder={
                      (detected.revisions?.length || 0) > 1
                        ? "Choose the returned revision"
                        : "Not stated · saved as Unspecified"
                    }
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
                  onClick={() => void download("source", item.filename)}
                >
                  <DownloadSimple size={15} />
                  Download original
                </button>
                <button className="primary" disabled={busy || !orderId}>
                  {busy ? "Adding…" : "Confirm and add"}
                </button>
              </footer>
              <button
                type="button"
                className="text-button intake-set-aside-action"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    confirmed(
                      await api<Intake>(
                        `/records/intake/${item.intake_id}/set-aside`,
                        {},
                      ),
                    );
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Not order-related · set aside
              </button>
              <p className="intake-message-note">
                The message and attachments stay together. Nothing is added to
                an order until you confirm.
              </p>
            </form>
          )}
        </div>
      )}
      {!assigning && suggestion && (
        <button
          className="text-button intake-other-order"
          onClick={() => {
            setAssigning(true);
            setExpanded(true);
          }}
        >
          Choose a different order
        </button>
      )}
    </article>
  );
}
