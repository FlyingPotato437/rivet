import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowClockwise,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCircle,
  ChatCircleText,
  Command,
  DownloadSimple,
  FileText,
  GitDiff,
  ListChecks,
  MagnifyingGlass,
  PaperPlaneRight,
  Plus,
  ShieldCheck,
  UploadSimple,
  WarningCircle,
  X,
  Clock,
  Copy,
  LinkSimple,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { Busy, Empty, ErrorNote, Mark, Modal } from "./ui";
import { EvidenceLinks, OrderEvidence } from "./OrderEvidence";
import type {
  CheckStatus,
  Obligation,
  OrderAction,
  OrderCheck,
  OrderCommandResult,
  OrderDocument,
  OrderHistory,
  OrderValue,
  OrderWorkspaceView,
  PropagationTask,
  RevisionDiff,
} from "./order-types";
import "./order-workspace.css";

type Tab = "review" | "obligations" | "sources" | "history" | "release";
type Selection = { kind: "check" | "action" | "comment"; id: string };
type Props = {
  id: string;
  navigate: (to: string) => void;
  notify: (message: string) => void;
  assistantReady: boolean;
};
const tabs: Tab[] = ["review", "obligations", "sources", "history", "release"];
const statusLabel: Record<string, string> = {
  pass: "Passing",
  fail: "Failing",
  unknown: "Needs evidence",
  conflict: "Conflict",
  waived: "Signed off",
  pending: "For your decision",
  accepted: "Accepted",
  rejected: "Rejected",
  stale: "Out of date",
  addressed: "Addressed",
  open: "Open",
  verified: "Verified",
  responded: "Response accepted",
};
export function orderValue(value: OrderValue | undefined): string {
  if (value == null || value === "") return "Not established";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value))
    return value.map((v) => orderValue(v as OrderValue)).join(", ");
  if (typeof value === "object")
    return Object.entries(value)
      .map(
        ([k, v]) => `${k.replaceAll("_", " ")}: ${orderValue(v as OrderValue)}`,
      )
      .join("\n");
  return String(value);
}
const attributeLabel = (key: string) =>
  (
    ({
      rated_voltage_v: "Rated voltage",
      main_bus_a: "Main bus rating",
      short_circuit_ka: "Short-circuit rating",
      short_time_ka: "Short-time withstand",
      short_time_duration_s: "Short-time duration",
      main_breaker_frame_a: "Main breaker frame",
      main_breaker_trip_a: "Main breaker trip",
      main_breaker_poles: "Main breaker poles",
      main_breaker_interrupting_ka: "Main breaker interrupting rating",
      feeder_breaker_frame_a: "Feeder breaker frame",
      feeder_breaker_trip_a: "Feeder breaker trip",
      control_voltage_v: "Control voltage",
      frequency_hz: "Frequency",
      neutral_bus_percent: "Neutral bus",
      enclosure_width_mm: "Enclosure width",
      enclosure_height_mm: "Enclosure height",
      enclosure_depth_mm: "Enclosure depth",
    }) as Record<string, string>
  )[key] ?? key.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
const tabFromLocation = (): Tab => {
  const p = new URLSearchParams(location.hash.split("?")[1] ?? "");
  const t = p.get("tab") ?? "review";
  return tabs.includes(t as Tab) ? (t as Tab) : "review";
};
function State({ status }: { status: string }) {
  const Icon = ["pass", "accepted", "verified", "addressed"].includes(status)
    ? CheckCircle
    : ["fail", "conflict"].includes(status)
      ? WarningCircle
      : status === "waived"
        ? ShieldCheck
        : Clock;
  return (
    <span className={`ow-state ${status}`}>
      <Icon size={13} />
      {statusLabel[status] ?? status.replaceAll("_", " ")}
    </span>
  );
}
function Datum({
  value,
  unit,
}: {
  value: OrderValue | undefined;
  unit?: string;
}) {
  return (
    <span>
      {orderValue(value)}
      {value != null && unit && <small> {unit}</small>}
    </span>
  );
}

export function OrderWorkspace({
  id,
  navigate,
  notify,
  assistantReady,
}: Props) {
  const qc = useQueryClient();
  const {
    data: w,
    error,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ["order", id],
    queryFn: () => api<OrderWorkspaceView>(`/orders/${id}`),
    refetchInterval: 3500,
  });
  const [tab, setTabState] = useState<Tab>(tabFromLocation);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [upload, setUpload] = useState(false);
  const [editingDocument, setEditingDocument] = useState<{
    document: OrderDocument;
    version: number;
  } | null>(null);
  const [waiver, setWaiver] = useState<OrderCheck | null>(null);
  const [release, setRelease] = useState(false);
  const [busy, setBusy] = useState("");
  const [actionError, setActionError] = useState("");
  const [prompt, setPrompt] = useState("");
  const [answer, setAnswer] = useState<OrderCommandResult | null>(null);
  const commandRef = useRef<HTMLTextAreaElement>(null);
  const refreshAll = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["order", id] }),
      qc.invalidateQueries({ queryKey: ["orders"] }),
      qc.invalidateQueries({ queryKey: ["order-feed"] }),
      qc.invalidateQueries({ queryKey: ["projects"] }),
    ]);
  };
  useEffect(() => {
    const sync = () => setTabState(tabFromLocation());
    window.addEventListener("hashchange", sync);
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        commandRef.current?.focus();
        commandRef.current?.scrollIntoView({
          block: "nearest",
          behavior: "smooth",
        });
      }
    };
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("keydown", key);
    };
  }, []);
  useEffect(() => {
    setSelection(null);
    setAnswer(null);
    setPrompt("");
    setActionError("");
    setTabState(tabFromLocation());
  }, [id]);
  const mutate = async (path: string, body: unknown, label: string) => {
    setBusy(label);
    setActionError("");
    try {
      await api(`/orders/${id}${path}`, body);
      await refreshAll();
      notify(label);
      return true;
    } catch (err) {
      setActionError((err as Error).message);
      await refreshAll();
      return false;
    } finally {
      setBusy("");
    }
  };
  const recheck = async () => {
    if (!w) return;
    await mutate(
      "/refresh",
      { expected_version: w.order.version },
      "Order checked against its current sources",
    );
  };
  const setTab = (next: Tab) => {
    setTabState(next);
    navigate(`order/${id}?tab=${next}`);
  };
  const openSource = (sid: string) => {
    setSourceId(sid);
    setDocumentId(null);
  };
  const links = (ids: string[]) => (
    <EvidenceLinks
      ids={ids}
      sources={w?.sources ?? []}
      documents={w?.documents ?? []}
      open={openSource}
    />
  );
  const reading = w?.documents.some((d) =>
    ["queued", "processing", "parsing", "extracting", "executing"].includes(
      d.state,
    ),
  );
  if (isLoading)
    return (
      <main className="ow-workspace">
        <Busy text="Opening the order…" />
      </main>
    );
  if (error || !w)
    return (
      <main className="ow-workspace">
        <ErrorNote
          message={
            (error as Error)?.message ?? "This order could not be opened."
          }
        />
        <button className="secondary" onClick={() => refetch()}>
          Try again
        </button>
      </main>
    );
  const { order } = w;
  const pending = w.actions.filter((a) => a.status === "pending");
  const failing = w.checks.filter((c) =>
    ["fail", "conflict", "unknown"].includes(c.status),
  );
  const current =
    selection ??
    (pending[0]
      ? { kind: "action", id: pending[0].id }
      : failing[0]
        ? { kind: "check", id: failing[0].id }
        : w.checks[0]
          ? { kind: "check", id: w.checks[0].id }
          : null);
  const selectedSource = w.sources.find((s) => s.id === sourceId);
  const shownDocument = w.documents.find(
    (d) => d.id === (documentId ?? selectedSource?.document_id),
  );
  const sendCommand = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!prompt.trim() || busy) return;
    setBusy("Preparing a response…");
    setActionError("");
    try {
      const result = await api<OrderCommandResult>(`/orders/${id}/commands`, {
        expected_version: order.version,
        prompt: prompt.trim(),
      });
      setAnswer(result);
      qc.setQueryData(["order", id], result.workspace);
      setPrompt("");
      await refreshAll();
      if (result.actions[0]) {
        setSelection({ kind: "action", id: result.actions[0].id });
        setTab("review");
      }
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy("");
    }
  };
  return (
    <main className="ow-workspace">
      <header className="ow-heading">
        <div>
          <button className="ow-back" onClick={() => navigate("projects")}>
            <ArrowLeft size={13} />
            All orders
          </button>
          <div className="ow-kicker">
            <span>{order.number}</span>
            <span>{order.customer || "Customer not set"}</span>
            {order.synthetic && <span className="ow-sample">Sample order</span>}
          </div>
          <h1>{order.title}</h1>
          <div className="ow-order-status">
            <strong>{order.revision_label || "No revision"}</strong>
            <span>{order.counts.checks} checks</span>
            <button
              onClick={() => {
                setTab("review");
                if (failing[0])
                  setSelection({ kind: "check", id: failing[0].id });
              }}
              className={order.counts.failing ? "has-failures" : ""}
            >
              {order.counts.failing} failing
            </button>
            {order.counts.unknown > 0 && (
              <span>{order.counts.unknown} need evidence</span>
            )}
            <button
              onClick={() => {
                setTab("review");
                if (pending[0])
                  setSelection({ kind: "action", id: pending[0].id });
              }}
              className={order.counts.decisions ? "has-decisions" : ""}
            >
              {order.counts.decisions}{" "}
              {order.counts.decisions === 1 ? "decision" : "decisions"} for you
            </button>
          </div>
        </div>
        <div className="ow-heading-actions">
          <button
            className="secondary"
            onClick={recheck}
            disabled={!!busy || reading}
          >
            <ArrowClockwise size={15} />
            {reading ? "Reading sources…" : "Run checks"}
          </button>
          <button className="primary" onClick={() => setUpload(true)}>
            <UploadSimple size={16} />
            Add revision
          </button>
        </div>
      </header>
      <div
        className="ow-check-line"
        aria-label={`${order.counts.passing} passing, ${order.counts.failing} failing, ${order.counts.unknown} need evidence, ${order.counts.waived} signed off`}
      >
        <i style={{ flex: order.counts.passing }} />
        <i className="fail" style={{ flex: order.counts.failing }} />
        <i className="unknown" style={{ flex: order.counts.unknown }} />
        <i className="waived" style={{ flex: order.counts.waived }} />
        {!order.counts.checks && <i className="empty" />}
      </div>
      <nav className="ow-tabs" aria-label="Order views">
        {tabs.map((t) => (
          <button
            key={t}
            className={tab === t ? "active" : ""}
            aria-current={tab === t ? "page" : undefined}
            onClick={() => setTab(t)}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
            {t === "review" && pending.length > 0 && (
              <span>{pending.length}</span>
            )}
            {t === "release" && (
              <span
                className={w.release.ready ? "ow-ready-dot" : "ow-blocked-dot"}
              />
            )}
          </button>
        ))}
        <span className="ow-version">Snapshot {order.version}</span>
      </nav>
      {actionError && <ErrorNote message={actionError} />}
      {reading && (
        <div className="ow-reading" role="status">
          <Busy text="Reading new evidence and preparing the next check run…" />
          <span>Your current review remains available.</span>
        </div>
      )}
      {!w.documents.length && (
        <div className="ow-get-started">
          <div className="ow-get-started-art">
            <FileText size={30} weight="thin" />
            <ArrowRight size={20} />
            <ListChecks size={30} weight="thin" />
          </div>
          <div>
            <h2>Add source documents</h2>
            <p>
              Add the specification, purchase order, approval drawings or
              engineer’s markups. Rivet builds the obligations and checks what
              changed.
            </p>
            <button className="primary" onClick={() => setUpload(true)}>
              <Plus size={16} />
              Add first documents
            </button>
          </div>
        </div>
      )}
      {tab === "review" && w.documents.length > 0 && (
        <Review
          workspace={w}
          selected={current}
          onSelect={setSelection}
          links={links}
          busy={!!busy}
          onAction={async (
            action,
            accept,
            reason,
            editedText,
            expectedVersion,
          ) => {
            await mutate(
              `/actions/${action.id}/${accept ? "accept" : "reject"}`,
              {
                expected_version: expectedVersion,
                reason:
                  reason.trim() ||
                  (accept
                    ? "Accepted after reviewing the proposal and its cited evidence."
                    : "Rejected after review."),
                ...(editedText === undefined
                  ? {}
                  : { edited_text: editedText }),
              },
              accept ? "Change accepted" : "Change rejected",
            );
          }}
          onWaive={setWaiver}
          onPrompt={(text) => {
            setPrompt(text);
            commandRef.current?.focus();
          }}
        />
      )}
      {tab === "obligations" && <Ledger ledger={w.ledger} links={links} />}
      {tab === "sources" && (
        <Sources
          documents={w.documents}
          onOpen={setDocumentId}
          onEdit={(document) =>
            setEditingDocument({ document, version: order.version })
          }
          onUpload={() => setUpload(true)}
        />
      )}
      {tab === "history" && (
        <History
          id={id}
          history={w.history}
          currentVersion={order.version}
          events={w.events}
          links={links}
        />
      )}
      {tab === "release" && (
        <Release
          workspace={w}
          busy={!!busy}
          onSign={() => setRelease(true)}
          links={links}
          onVerify={(task) =>
            void mutate(
              `/tasks/${task.id}/verify`,
              {
                expected_version: order.version,
                source_ids: task.actual_source_ids,
              },
              "Propagation evidence checked",
            )
          }
          onUpload={() => setUpload(true)}
        />
      )}
      <section className="ow-command" aria-label="Rivet command bar">
        <div className="ow-command-top">
          <div>
            <Mark small />
            <strong>Rivet</strong>
            <span>Ask about the order. Review the proposed change.</span>
          </div>
          <kbd>⌘ K</kbd>
        </div>
        {answer && (
          <div className="ow-command-answer">
            <button
              className="icon-button ow-dismiss-answer"
              aria-label="Dismiss answer"
              onClick={() => setAnswer(null)}
            >
              <X size={15} />
            </button>
            <p>{answer.answer}</p>
            {links(answer.source_ids)}
            {answer.actions.length > 0 && (
              <button
                className="text-button"
                onClick={() => {
                  setTab("review");
                  setSelection({ kind: "action", id: answer.actions[0].id });
                  setAnswer(null);
                }}
              >
                Review {answer.actions.length} proposed{" "}
                {answer.actions.length === 1 ? "change" : "changes"}
                <ArrowRight size={14} />
              </button>
            )}
          </div>
        )}
        <form onSubmit={sendCommand}>
          <textarea
            ref={commandRef}
            rows={1}
            aria-label="Ask Rivet about this order"
            placeholder="What changed? What’s blocked? Draft a reply…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void sendCommand();
              }
            }}
          />
          <button
            className="ow-command-send"
            disabled={!prompt.trim() || !!busy}
            aria-label="Send order command"
          >
            {busy ? <Busy text="" /> : <ArrowRight size={19} />}
          </button>
        </form>
        <div className="ow-command-examples">
          {[
            "What changed since the previous revision?",
            "Why is release blocked?",
            "Draft the transmittal",
          ].map((text) => (
            <button
              key={text}
              onClick={() => {
                setPrompt(text);
                commandRef.current?.focus();
              }}
            >
              {text}
              <ArrowUpRight size={11} />
            </button>
          ))}
        </div>
        {!assistantReady && !w.capabilities.ai_configured && (
          <p className="ow-command-note">
            Source-backed checks and summaries are available. Connect an AI
            provider in Settings for language assistance.
          </p>
        )}
      </section>
      {shownDocument && (
        <OrderEvidence
          document={shownDocument}
          sources={w.sources.filter((s) => s.document_id === shownDocument.id)}
          selectedId={sourceId ?? undefined}
          close={() => {
            setSourceId(null);
            setDocumentId(null);
          }}
        />
      )}
      {upload && (
        <Upload
          projectId={order.project_id}
          close={() => setUpload(false)}
          done={async () => {
            setUpload(false);
            await refreshAll();
            notify(
              "Sources added. Rivet will recheck the order after reading.",
            );
          }}
        />
      )}
      {editingDocument && (
        <DocumentDetails
          orderId={id}
          document={editingDocument.document}
          version={editingDocument.version}
          close={() => setEditingDocument(null)}
          done={async () => {
            setEditingDocument(null);
            await refreshAll();
            notify("Document details updated. Rivet rechecked the order.");
          }}
        />
      )}
      {waiver && (
        <SignOff
          title="Sign off this finding"
          eyebrow={waiver.title}
          description="Record who accepts this exception and why. The sign-off applies only to this exact finding and evidence; changed evidence requires a new review."
          action="Sign exception"
          onClose={() => setWaiver(null)}
          version={order.version}
          onSubmit={async (signer, reason, expectedVersion) => {
            const okay = await mutate(
              "/waivers",
              {
                expected_version: expectedVersion,
                check_id: waiver.id,
                fingerprint: waiver.fingerprint,
                signer,
                reason,
              },
              "Exception signed and recorded",
            );
            if (okay) setWaiver(null);
            return okay;
          }}
        />
      )}
      {release && (
        <SignOff
          title="Approve release to production"
          eyebrow={`${order.revision_label} · Snapshot ${order.version}`}
          description="Your signature records approval of this exact order snapshot. Any subsequent change invalidates this release."
          action="Approve release"
          version={order.version}
          onClose={() => setRelease(false)}
          onSubmit={async (signer, reason, expectedVersion) => {
            const okay = await mutate(
              "/release",
              {
                expected_version: expectedVersion,
                snapshot_hash: order.snapshot_hash,
                signer,
                reason,
              },
              "Order released to production",
            );
            if (okay) setRelease(false);
            return okay;
          }}
        />
      )}
    </main>
  );
}

type Links = (ids: string[]) => ReactNode;
function Review({
  workspace: w,
  selected,
  onSelect,
  links,
  busy,
  onAction,
  onWaive,
  onPrompt,
}: {
  workspace: OrderWorkspaceView;
  selected: Selection | null;
  onSelect: (s: Selection) => void;
  links: Links;
  busy: boolean;
  onAction: (
    a: OrderAction,
    accept: boolean,
    reason: string,
    edited: string | undefined,
    expectedVersion: number,
  ) => Promise<void>;
  onWaive: (c: OrderCheck) => void;
  onPrompt: (text: string) => void;
}) {
  const [filter, setFilter] = useState("open");
  const detailRef = useRef<HTMLDivElement>(null);
  const choose = (selection: Selection) => {
    onSelect(selection);
    requestAnimationFrame(() => {
      if (matchMedia("(max-width:640px)").matches)
        detailRef.current?.scrollIntoView({
          block: "start",
          behavior: matchMedia("(prefers-reduced-motion:reduce)").matches
            ? "instant"
            : "smooth",
        });
      else if (detailRef.current) detailRef.current.scrollTop = 0;
    });
  };
  const actions = w.actions.filter((a) =>
    filter === "all" || filter === "decisions"
      ? filter === "all" || a.status === "pending"
      : a.status === "pending",
  );
  const checks = w.checks.filter(
    (c) =>
      filter !== "decisions" &&
      (filter === "all" || !["pass", "waived"].includes(c.status)),
  );
  const comments =
    filter === "decisions"
      ? []
      : w.comments.filter((c) => filter === "all" || c.status !== "addressed");
  const action =
    selected?.kind === "action"
      ? w.actions.find((a) => a.id === selected.id)
      : null;
  const check =
    selected?.kind === "check"
      ? w.checks.find((c) => c.id === selected.id)
      : null;
  const comment =
    selected?.kind === "comment"
      ? w.comments.find((c) => c.id === selected.id)
      : null;
  return (
    <section className="ow-review">
      <div className="ow-review-index">
        <header>
          <h2>Review queue</h2>
          <span>{actions.length + checks.length + comments.length}</span>
        </header>
        <div className="ow-filters" aria-label="Review filters">
          {[
            ["open", "Open"],
            ["decisions", "Decisions"],
            ["all", "All"],
          ].map(([v, label]) => (
            <button
              key={v}
              className={filter === v ? "active" : ""}
              onClick={() => setFilter(v)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="ow-review-list">
          {actions.length > 0 && (
            <div className="ow-list-label">For your decision</div>
          )}
          {actions.map((a) => (
            <button
              className={`ow-review-item ${selected?.id === a.id ? "selected" : ""}`}
              key={a.id}
              onClick={() => choose({ kind: "action", id: a.id })}
            >
              <GitDiff size={16} />
              <span>
                <strong>{a.title}</strong>
                <small>
                  {a.type.replaceAll("_", " ")} · {statusLabel[a.status]}
                </small>
              </span>
              <ArrowRight size={13} />
            </button>
          ))}
          {checks.length > 0 && <div className="ow-list-label">Checks</div>}
          {checks.map((c) => (
            <button
              className={`ow-review-item ${selected?.id === c.id ? "selected" : ""}`}
              key={c.id}
              onClick={() => choose({ kind: "check", id: c.id })}
            >
              {c.status === "pass" ? (
                <CheckCircle className="ow-positive" size={16} />
              ) : (
                <WarningCircle
                  className={c.status === "fail" ? "ow-negative" : ""}
                  size={16}
                />
              )}
              <span>
                <strong>{c.title}</strong>
                <small>
                  {c.device} · {statusLabel[c.status]}
                </small>
              </span>
              <ArrowRight size={13} />
            </button>
          ))}
          {comments.length > 0 && (
            <div className="ow-list-label">Engineer’s comments</div>
          )}
          {comments.map((c) => (
            <button
              className={`ow-review-item ${selected?.id === c.id ? "selected" : ""}`}
              key={c.id}
              onClick={() => choose({ kind: "comment", id: c.id })}
            >
              <ChatCircleText size={16} />
              <span>
                <strong>Comment {c.number}</strong>
                <small>{c.text}</small>
              </span>
              <ArrowRight size={13} />
            </button>
          ))}
          {!actions.length && !checks.length && !comments.length && (
            <div className="ow-queue-clear">
              <CheckCircle size={24} />
              <strong>No open items</strong>
              <p>
                {filter === "decisions"
                  ? "Proposed changes will appear here for your approval."
                  : "Check the release view for the remaining production requirements."}
              </p>
            </div>
          )}
        </div>
      </div>
      <div className="ow-review-detail" ref={detailRef}>
        {action ? (
          <ActionReview
            key={action.id}
            action={action}
            version={w.order.version}
            links={links}
            busy={busy}
            submit={onAction}
          />
        ) : check ? (
          <>
            <div className="ow-detail-kicker">
              <span>
                {check.device} / {attributeLabel(check.attribute)}
              </span>
              <State status={check.status} />
            </div>
            <h2>{check.title}</h2>
            <p className="ow-detail-intro">{check.detail}</p>
            <div className="ow-value-comparison">
              <div>
                <span>Effective obligation</span>
                <strong>
                  <Datum value={check.expected} unit={check.unit} />
                </strong>
                {links(check.source_ids)}
              </div>
              <ArrowRight size={22} />
              <div>
                <span>Current drawing / evidence</span>
                <strong>
                  <Datum value={check.actual} unit={check.unit} />
                </strong>
                {links(check.actual_source_ids)}
              </div>
            </div>
            <div className="ow-review-explanation">
              <ShieldCheck size={19} />
              <div>
                <h3>Resolution requirements</h3>
                <p>
                  A reply or a completed task does not pass this check. The
                  revised source must show the required value.
                </p>
              </div>
            </div>
            {check.waiver && (
              <blockquote className="ow-citation">
                <span>Signed by {check.waiver.signer}</span>
                <p>{check.waiver.reason}</p>
              </blockquote>
            )}
            <footer className="ow-detail-actions">
              <button
                className="primary"
                onClick={() =>
                  onPrompt(
                    `Draft a response for ${check.device}: ${check.title}`,
                  )
                }
              >
                <ChatCircleText size={15} />
                Draft a response
              </button>
              {!["pass", "waived"].includes(check.status) &&
                check.id !== "coverage" &&
                !check.id.startsWith("propagation:") && (
                  <button className="secondary" onClick={() => onWaive(check)}>
                    Sign an exception
                  </button>
                )}
            </footer>
          </>
        ) : comment ? (
          <>
            <div className="ow-detail-kicker">
              <span>Engineer’s comment {comment.number}</span>
              <State status={comment.status} />
            </div>
            <h2>{comment.device || "Review comment"}</h2>
            <blockquote className="ow-comment-quote">{comment.text}</blockquote>
            {links(comment.source_ids)}
            {comment.response && (
              <div className="ow-response-text">
                <span>Current response</span>
                <p>{comment.response}</p>
              </div>
            )}
            <div className="ow-review-explanation">
              <ListChecks size={19} />
              <div>
                <h3>Comment status</h3>
                <p>
                  Rivet checks the next drawing revision against this comment
                  before marking it addressed.
                </p>
              </div>
            </div>
            <button
              className="primary"
              onClick={() => onPrompt(`Reply to comment ${comment.number}: `)}
            >
              <ChatCircleText size={15} />
              Draft reply
            </button>
          </>
        ) : (
          <Empty
            icon={<ListChecks size={26} />}
            title="The review is ready for you"
          >
            Select a decision, check or comment to see the evidence and proposed
            response.
          </Empty>
        )}
      </div>
    </section>
  );
}
function CopyDraft({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setCopied(false);
    setError("");
  }, [text]);
  return (
    <>
      <button
        className="secondary"
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setError("");
          } catch {
            setError(
              "Clipboard access was unavailable. Select the draft text to copy it.",
            );
          }
        }}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}{" "}
        {copied ? "Copied" : "Copy draft"}
      </button>
      {error && (
        <p className="ow-copy-error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}

function ActionValue({
  value,
  context,
}: {
  value: OrderValue;
  context: OrderValue;
}) {
  if (value == null) return <pre>No existing value</pre>;
  if (typeof value !== "object" || Array.isArray(value))
    return <pre>{orderValue(value)}</pre>;
  if (typeof value.text === "string")
    return <pre>{value.text || "No response yet"}</pre>;
  if ("value" in value) {
    const details =
      typeof context === "object" && context && !Array.isArray(context)
        ? context
        : {};
    return (
      <div className="ow-diff-config">
        <span>
          {String(value.device ?? details.device ?? "")} ·{" "}
          {attributeLabel(String(value.attribute ?? details.attribute ?? ""))}
        </span>
        <strong>
          {orderValue(value.value as OrderValue)}
          <small> {String(value.unit ?? details.unit ?? "")}</small>
        </strong>
      </div>
    );
  }
  if (value.accept_exception_document)
    return (
      <pre>
        Recognize the cited document as an accepted exception. Rivet will
        recalculate the effective obligations using the order’s precedence
        rules.
      </pre>
    );
  if (value.alias && value.device)
    return (
      <pre>
        {String(value.alias)} → {String(value.device)}
      </pre>
    );
  const visible = Object.fromEntries(
    Object.entries(value).filter(
      ([key]) => !key.endsWith("_id") && !key.endsWith("_ids"),
    ),
  );
  return (
    <pre>
      {Object.keys(visible).length ? orderValue(visible) : "No existing value"}
    </pre>
  );
}

function ActionReview({
  action: a,
  version,
  links,
  busy,
  submit,
}: {
  action: OrderAction;
  version: number;
  links: Links;
  busy: boolean;
  submit: (
    a: OrderAction,
    accept: boolean,
    reason: string,
    edited: string | undefined,
    version: number,
  ) => Promise<void>;
}) {
  const [expectedVersion, setExpectedVersion] = useState(version);
  const [reason, setReason] = useState("");
  const [editing, setEditing] = useState(false);
  const initialText =
    typeof a.after === "string"
      ? a.after
      : typeof a.after === "object" && a.after && !Array.isArray(a.after)
        ? String(
            a.after.text ??
              a.after.response ??
              a.after.body ??
              orderValue(a.after),
          )
        : orderValue(a.after);
  const [edited, setEdited] = useState(initialText);
  const editable = ["response", "rfi", "task"].includes(a.type);
  const isPending = a.status === "pending";
  return (
    <>
      <div className="ow-detail-kicker">
        <span>Proposed {a.type.replaceAll("_", " ")}</span>
        <State status={a.status} />
      </div>
      <h2>{a.title}</h2>
      <p className="ow-detail-intro">{a.summary}</p>
      <div
        className={`ow-action-diff ${a.type === "configuration" && typeof a.after === "object" && a.after && "value" in a.after ? "is-configuration" : ""}`}
      >
        <div className="ow-diff-before">
          <span>Current</span>
          <ActionValue value={a.before} context={a.after} />
          {typeof a.before === "object" &&
            a.before &&
            !Array.isArray(a.before) &&
            Array.isArray(a.before.source_ids) &&
            links(
              a.before.source_ids.filter(
                (id): id is string => typeof id === "string",
              ),
            )}
        </div>
        <div className="ow-diff-after">
          <span>Proposed</span>
          {editing ? (
            <textarea
              value={edited}
              rows={8}
              aria-label="Edit proposed response"
              onChange={(e) => setEdited(e.target.value)}
            />
          ) : (
            <ActionValue value={a.after} context={a.after} />
          )}
        </div>
      </div>
      {links(a.source_ids)}
      {isPending && (
        <>
          <label className="ow-reason">
            Decision note <span>Optional</span>
            <input
              placeholder="Add context for the order history…"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {version !== expectedVersion && (
            <p className="ow-stale-note">
              <WarningCircle size={14} />
              The order changed while this decision was open. Review the current
              diff before approving.
              <button
                type="button"
                className="text-button"
                onClick={() => setExpectedVersion(version)}
              >
                Use current snapshot
              </button>
            </p>
          )}
          <footer className="ow-detail-actions">
            <button
              className="primary"
              disabled={
                busy ||
                version !== expectedVersion ||
                (editing && !edited.trim())
              }
              onClick={() =>
                void submit(
                  a,
                  true,
                  reason,
                  editing ? edited : undefined,
                  expectedVersion,
                )
              }
            >
              <Check size={16} />
              Accept {editable ? "draft" : "change"}
            </button>
            <button
              className="secondary"
              disabled={busy || version !== expectedVersion}
              onClick={() =>
                void submit(a, false, reason, undefined, expectedVersion)
              }
            >
              <X size={15} />
              Reject
            </button>
            {editable && (
              <button
                className="text-button"
                onClick={() => setEditing(!editing)}
              >
                {editing ? "Cancel edit" : "Edit response"}
              </button>
            )}
          </footer>
          <p className="ow-draft-note">
            {editable
              ? "Accepted drafts stay in Rivet for export. Nothing is sent to the customer."
              : "Accepting records this decision and re-runs the affected checks."}
          </p>
        </>
      )}
      {!isPending && editable && a.status === "accepted" && (
        <div className="ow-approved-draft-copy">
          <CopyDraft
            text={
              typeof a.after === "object" && a.after && !Array.isArray(a.after)
                ? String(a.after.text ?? initialText)
                : initialText
            }
          />
        </div>
      )}
      {!isPending && a.reason && (
        <blockquote className="ow-citation">
          <span>Decision note</span>
          <p>{a.reason}</p>
        </blockquote>
      )}
    </>
  );
}
function Ledger({ ledger, links }: { ledger: Obligation[]; links: Links }) {
  const [query, setQuery] = useState("");
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const shown = ledger.filter(
    (item) =>
      (!onlyOpen || !["pass", "waived"].includes(item.status)) &&
      `${item.device} ${item.label} ${orderValue(item.expected)} ${orderValue(item.actual)}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <section className="ow-ledger">
      <div className="ow-section-heading">
        <div>
          <h2>Obligations</h2>
          <p>Effective requirements, reconciled from the source documents.</p>
        </div>
        <span className="ow-precedence">
          PO <ArrowRight size={11} /> Accepted exceptions{" "}
          <ArrowRight size={11} /> Specification <ArrowRight size={11} />{" "}
          Drawings
        </span>
      </div>
      <div className="ow-ledger-tools">
        <label className="ow-search">
          <MagnifyingGlass size={16} />
          <input
            aria-label="Search obligations"
            placeholder="Find a device or requirement…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <button
          className={onlyOpen ? "secondary active" : "secondary"}
          aria-pressed={onlyOpen}
          onClick={() => setOnlyOpen(!onlyOpen)}
        >
          Only unresolved
        </button>
        <span>{shown.length} obligations</span>
      </div>
      <div className="ow-ledger-table">
        <div className="ow-ledger-head">
          <span>Device / obligation</span>
          <span>Effective requirement</span>
          <span>Current evidence</span>
          <span>Check</span>
        </div>
        {shown.map((item) => (
          <div className="ow-ledger-entry" key={item.id}>
            <button
              className="ow-ledger-row"
              onClick={() => setExpanded(expanded === item.id ? null : item.id)}
              aria-expanded={expanded === item.id}
            >
              <span>
                <strong>{item.device}</strong>
                <small>{item.label}</small>
              </span>
              <span>
                <Datum value={item.expected} unit={item.unit} />
              </span>
              <span>
                <Datum value={item.actual} unit={item.unit} />
              </span>
              <State status={item.status} />
            </button>
            {expanded === item.id && (
              <div className="ow-ledger-evidence">
                <div>
                  <span>Why this requirement applies</span>
                  <p>
                    {item.precedence.replaceAll("_", " ")} establishes this
                    requirement.
                  </p>
                  {links(item.source_ids)}
                </div>
                <div>
                  <span>Drawing evidence</span>
                  {links(item.actual_source_ids)}
                  {!item.actual_source_ids.length && (
                    <p>No source establishes the current value.</p>
                  )}
                  {item.conflict_values?.length > 0 && (
                    <p>
                      Conflicting values:{" "}
                      {item.conflict_values
                        .map((v) => orderValue(v))
                        .join(" / ")}
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
        {!shown.length && (
          <Empty
            title={
              ledger.length
                ? "No matching obligations"
                : "The ledger will build from your documents"
            }
          >
            {ledger.length
              ? "Try another device or clear the unresolved filter."
              : "Add a purchase order, specification and drawing revision to establish the order requirements."}
          </Empty>
        )}
      </div>
    </section>
  );
}
function Sources({
  documents,
  onOpen,
  onEdit,
  onUpload,
}: {
  documents: OrderDocument[];
  onOpen: (id: string) => void;
  onEdit: (document: OrderDocument) => void;
  onUpload: () => void;
}) {
  return (
    <section className="ow-sources">
      <div className="ow-section-heading">
        <div>
          <h2>Source documents</h2>
          <p>
            View originals, source coverage, and document revisions.
          </p>
        </div>
        <button className="secondary" onClick={onUpload}>
          <UploadSimple size={15} />
          Add documents
        </button>
      </div>
      <div className="ow-doc-list">
        {documents.map((d) => (
          <div className="ow-document-row" key={d.id}>
            <button className="ow-document" onClick={() => onOpen(d.id)}>
              <span className="ow-file-icon">
                <FileText size={21} />
              </span>
              <span>
                <strong>{d.name}</strong>
                <small>
                  {(d.role || d.kind).replaceAll("_", " ")}
                  {d.revision_label ? ` · ${d.revision_label}` : ""}
                </small>
                {d.warnings?.length > 0 && <em>{d.warnings.join(" · ")}</em>}
              </span>
              <span
                className={
                  d.state === "failed" ? "ow-negative" : "ow-document-state"
                }
              >
                {d.state.replaceAll("_", " ")}
              </span>
              <ArrowUpRight size={16} />
            </button>
            <button
              className="ow-doc-edit"
              disabled={["queued", "processing", "parsing"].includes(d.state)}
              title={
                ["queued", "processing", "parsing"].includes(d.state)
                  ? "Document details can be edited when reading finishes."
                  : undefined
              }
              onClick={() => onEdit(d)}
              aria-label={`Edit details for ${d.name}`}
            >
              Edit details
            </button>
          </div>
        ))}
      </div>
      {!documents.length && (
        <Empty
          title="No source documents yet"
          action={
            <button className="primary" onClick={onUpload}>
              Add documents
            </button>
          }
        >
          Start with the existing approval package and its requirements.
        </Empty>
      )}
      <div className="ow-sources-foot">
        <LinkSimple size={16} />
        <p>
          Local uploads are available now. Shared inbox, network drive and
          native CAD connections are not connected.
        </p>
      </div>
    </section>
  );
}
function History({
  id,
  history,
  currentVersion,
  events,
  links,
}: {
  id: string;
  history: OrderHistory[];
  currentVersion: number;
  events: OrderWorkspaceView["events"];
  links: Links;
}) {
  const ordered = [...history].sort((a, b) => b.version - a.version);
  const currentLabel = ordered.find(
    (h) => h.version === currentVersion,
  )?.revision_label;
  const defaultFrom =
    ordered.find(
      (h) =>
        !h.is_processing &&
        h.version < currentVersion &&
        h.revision_label !== currentLabel,
    ) ?? ordered.find((h) => !h.is_processing && h.version < currentVersion);
  const [from, setFrom] = useState(defaultFrom?.version ?? currentVersion);
  const [to, setTo] = useState(currentVersion);
  const fromRevision = history.find((h) => h.version === from);
  const toRevision = history.find((h) => h.version === to);
  const diff = useQuery({
    queryKey: ["order-diff", id, from, to],
    queryFn: () =>
      api<RevisionDiff>(
        `/orders/${id}/diff?from_version=${from}&to_version=${to}`,
      ),
    enabled: history.length > 1 && from !== to,
  });
  const changed = (diff.data?.changes ?? []).filter(
    (c) =>
      JSON.stringify(c.before) !== JSON.stringify(c.after) ||
      JSON.stringify(c.expected_before) !== JSON.stringify(c.expected_after) ||
      c.status_before !== c.status_after,
  );
  const evidenceOnly = (diff.data?.changes ?? []).filter(
    (c) => !changed.includes(c),
  );
  return (
    <section className="ow-history">
      <div className="ow-section-heading">
        <div>
          <h2>Revision history</h2>
          <p>
            Compare requirements and drawing values between saved revisions.
          </p>
        </div>
      </div>
      {history.length > 1 && (
        <>
          <div className="ow-compare-toolbar">
            <GitDiff size={18} />
            <label>
              From
              <select
                aria-label="Compare from revision"
                value={from}
                onChange={(e) => setFrom(Number(e.target.value))}
              >
                {ordered.map((h) => (
                  <option
                    key={h.version}
                    value={h.version}
                    disabled={h.is_processing}
                  >
                    {h.revision_label} · snapshot {h.version}
                    {h.is_processing ? " · reading sources" : ""}
                  </option>
                ))}
              </select>
            </label>
            <ArrowRight size={17} />
            <label>
              To
              <select
                aria-label="Compare to revision"
                value={to}
                onChange={(e) => setTo(Number(e.target.value))}
              >
                {ordered.map((h) => (
                  <option
                    key={h.version}
                    value={h.version}
                    disabled={h.is_processing}
                  >
                    {h.revision_label} · snapshot {h.version}
                    {h.is_processing ? " · reading sources" : ""}
                  </option>
                ))}
              </select>
            </label>
            <a
              className="secondary"
              href={`/api/orders/${id}/exports/bom-delta?from_version=${from}&to_version=${to}`}
            >
              <DownloadSimple size={15} />
              BOM delta
            </a>
          </div>
          {diff.isLoading && <Busy text="Comparing revisions…" />}
          {diff.error && <ErrorNote message={(diff.error as Error).message} />}
          <div className="ow-history-comparison">
            {changed.map((change, index) => (
              <article key={`${change.device}-${change.attribute}-${index}`}>
                <header>
                  <span>
                    <strong>{change.device}</strong>
                    <small>{attributeLabel(change.attribute)}</small>
                  </span>
                  <span className="ow-history-check-change">
                    <State status={change.status_before ?? "unknown"} />
                    <ArrowRight size={11} />
                    <State status={change.status_after ?? "unknown"} />
                  </span>
                </header>
                <div className="ow-history-diff-grid">
                  <div>
                    <span>
                      {fromRevision?.revision_label} · Snapshot {from}
                    </span>
                    <dl>
                      <div>
                        <dt>Requirement</dt>
                        <dd>
                          <Datum
                            value={change.expected_before}
                            unit={change.unit}
                          />
                        </dd>
                      </div>
                      <div>
                        <dt>Drawing</dt>
                        <dd>
                          <Datum value={change.before} unit={change.unit} />
                        </dd>
                      </div>
                    </dl>
                    {links(change.before_source_ids ?? [])}
                  </div>
                  <div>
                    <span>
                      {toRevision?.revision_label} · Snapshot {to}
                    </span>
                    <dl>
                      <div>
                        <dt>Requirement</dt>
                        <dd>
                          <Datum
                            value={change.expected_after}
                            unit={change.unit}
                          />
                        </dd>
                      </div>
                      <div>
                        <dt>Drawing</dt>
                        <dd>
                          <Datum value={change.after} unit={change.unit} />
                        </dd>
                      </div>
                    </dl>
                    {links(change.source_ids)}
                  </div>
                </div>
              </article>
            ))}
            {from === to ? (
              <p>Select two different snapshots to compare.</p>
            ) : (
              diff.data &&
              !changed.length && (
                <p>
                  No equipment values or check results changed between these
                  snapshots.
                </p>
              )
            )}
          </div>
          {evidenceOnly.length > 0 && (
            <details className="ow-evidence-refresh">
              <summary>
                Evidence refreshed for {evidenceOnly.length} unchanged{" "}
                {evidenceOnly.length === 1 ? "value" : "values"}
                <span>Values and checks are unchanged</span>
              </summary>
              {evidenceOnly.map((change, index) => (
                <div key={`${change.device}-${change.attribute}-${index}`}>
                  <strong>
                    {change.device} · {attributeLabel(change.attribute)}
                  </strong>
                  <div>
                    <span>{fromRevision?.revision_label}</span>
                    {links(change.before_source_ids ?? [])}
                    <ArrowRight size={12} />
                    <span>{toRevision?.revision_label}</span>
                    {links(change.source_ids)}
                  </div>
                </div>
              ))}
            </details>
          )}
        </>
      )}
      <div className="ow-history-list">
        {ordered.map((h) => (
          <article key={h.version}>
            <span className="ow-history-node">
              <GitDiff size={14} />
            </span>
            <div>
              <div className="ow-history-meta">
                <strong>
                  {h.revision_label}{" "}
                  <span>
                    · Snapshot {h.version}
                    {h.is_processing ? " · Reading sources" : ""}
                  </span>
                </strong>
                <time>{when(h.at)}</time>
              </div>
              <p>{h.summary}</p>
              <small>{h.actor}</small>
            </div>
          </article>
        ))}
        {!history.length && (
          <Empty title="No revisions">
            New evidence and accepted decisions will create traceable snapshots.
          </Empty>
        )}
      </div>
      {events.length > 0 && (
        <details className="ow-events">
          <summary>
            View event log <span>{events.length}</span>
          </summary>
          {events.map((event) => (
            <div key={event.id}>
              <span>{event.summary}</span>
              <small>
                {event.actor} · {when(event.at)}
              </small>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}

function Release({
  workspace: w,
  busy,
  onSign,
  links,
  onVerify,
  onUpload,
}: {
  workspace: OrderWorkspaceView;
  busy: boolean;
  onSign: () => void;
  links: Links;
  onVerify: (task: PropagationTask) => void;
  onUpload: () => void;
}) {
  const released = w.release.approval?.version === w.order.version;
  return (
    <section className="ow-release">
      <div className={`ow-release-banner ${w.release.ready ? "ready" : ""}`}>
        <ShieldCheck size={30} weight="thin" />
        <div>
          <span>Production release</span>
          <h2>
            {released
              ? "Released"
              : w.release.ready
                ? "Ready for approval"
                : "Release blocked"}
          </h2>
          <p>
            {released
              ? `Approved by ${w.release.approval?.signer} · ${when(w.release.approval!.at)}`
              : w.release.ready
                ? "Checks have passed or carry a signed exception. Approve this exact snapshot to release it."
                : "Every check must pass or carry a signed exception. Propagated changes need evidence before release."}
          </p>
        </div>
        <button
          className="primary"
          disabled={!w.release.ready || released || busy}
          onClick={onSign}
        >
          <ShieldCheck size={16} />
          {released ? "Released" : "Approve release"}
        </button>
      </div>
      {w.release.blockers.length > 0 && (
        <div className="ow-blockers">
          <h3>Release blockers</h3>
          {w.release.blockers.map((reason, index) => (
            <div key={index}>
              <WarningCircle size={16} />
              <span>{reason}</span>
            </div>
          ))}
        </div>
      )}
      <div className="ow-section-heading">
        <div>
          <h2>Downstream verification</h2>
          <p>
            Verify that the BOM, supplier purchase order and nameplates reflect
            the approved configuration.
          </p>
        </div>
        <button className="secondary" onClick={onUpload}>
          <UploadSimple size={15} />
          Add updated evidence
        </button>
      </div>
      <div className="ow-propagation">
        {w.tasks.map((task) => (
          <article key={task.id}>
            <div className="ow-task-title">
              <span className={`ow-task-dot ${task.status}`} />
              <div>
                <h3>{task.title}</h3>
                <span>
                  {task.target.replaceAll("_", " ")} · {task.device}
                </span>
              </div>
              {task.status === "pending" ? (
                <span className="ow-state pending">Awaiting evidence</span>
              ) : (
                <State status={task.status} />
              )}
            </div>
            <div className="ow-task-values">
              <span>
                Expected{" "}
                <strong>
                  <Datum value={task.expected} />
                </strong>
              </span>
              <ArrowRight size={15} />
              <span>
                Evidence{" "}
                <strong>
                  <Datum value={task.actual} />
                </strong>
              </span>
            </div>
            {links(
              task.actual_source_ids.length
                ? task.actual_source_ids
                : task.source_ids,
            )}
            {task.status !== "verified" && (
              <button
                className="text-button"
                disabled={busy}
                onClick={() => onVerify(task)}
              >
                Verify against current sources
                <ArrowRight size={14} />
              </button>
            )}
          </article>
        ))}
        {!w.tasks.length && (
          <div className="ow-no-tasks">
            <CheckCircle size={20} />
            <p>No propagation items have been created for this snapshot.</p>
          </div>
        )}
      </div>
      {!!w.drafts?.length && (
        <section className="ow-approved-drafts">
          <h3>Approved drafts</h3>
          <p>Accepted RFIs, task instructions, and transmittals.</p>
          {w.drafts.map((draft) => (
            <details key={draft.id}>
              <summary>
                {draft.title}
                <span>{draft.type.replaceAll("_", " ")}</span>
              </summary>
              <p>{draft.text}</p>
              {links(draft.source_ids)}
              <CopyDraft text={draft.text} />
            </details>
          ))}
        </section>
      )}
      <div className="ow-output-list">
        <div>
          <h3>Exports</h3>
          <p>
            Export accepted responses and revision changes. Customer
            communication stays in your control.
          </p>
        </div>
        <a
          className="secondary"
          href={`/api/orders/${w.order.id}/exports/response-matrix?format=pdf`}
        >
          <DownloadSimple size={15} />
          Response matrix PDF
        </a>
        <a
          className="secondary"
          href={`/api/orders/${w.order.id}/exports/response-matrix?format=xlsx`}
        >
          <DownloadSimple size={15} />
          Response matrix Excel
        </a>
      </div>
    </section>
  );
}
const documentRoles = [
  ["purchase_order", "Customer purchase order"],
  ["accepted_exception", "Accepted exception"],
  ["specification", "Specification"],
  ["drawing", "Approval drawing"],
  ["markup", "Engineer’s markups"],
  ["bom", "Bill of materials"],
  ["supplier_po", "Supplier purchase order"],
  ["nameplate", "Nameplate"],
  ["quote", "Quote"],
];
function DocumentDetails({
  orderId,
  document,
  version,
  close,
  done,
}: {
  orderId: string;
  document: OrderDocument;
  version: number;
  close: () => void;
  done: () => Promise<void>;
}) {
  const [role, setRole] = useState(
    documentRoles.some(([value]) => value === document.role)
      ? document.role
      : "",
  );
  const [revision, setRevision] = useState(document.revision_label ?? "");
  const [expectedVersion] = useState(version);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal
      title="Edit document details"
      eyebrow={document.name}
      onClose={() => {
        if (!busy) close();
      }}
    >
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!role || busy) return;
          setBusy(true);
          setError("");
          try {
            await api(`/orders/${orderId}/documents/${document.id}/classify`, {
              expected_version: expectedVersion,
              role,
              revision_label: revision.trim() || null,
            });
            await done();
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="ow-document-details-copy">
          Correct how this document is used in the order. Rivet retains the
          original file and cited text, then recalculates the affected
          obligations and checks.
        </p>
        <label>
          Document role
          <select
            required
            value={role}
            onChange={(event) => setRole(event.target.value)}
          >
            <option value="" disabled>
              Choose the document’s role
            </option>
            {documentRoles.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Revision label{" "}
          <input
            value={revision}
            maxLength={40}
            placeholder="For example, Rev C"
            onChange={(event) => setRevision(event.target.value)}
          />
          <span className="ow-document-details-hint">
            Leave blank to use the revision found in the file name or document.
          </span>
        </label>
        {role === "accepted_exception" && (
          <p className="ow-document-details-note">
            This identifies an exception document. It still needs an explicit
            acceptance decision before it can take precedence.
          </p>
        )}
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <button
            className="secondary"
            type="button"
            disabled={busy}
            onClick={close}
          >
            Cancel
          </button>
          <button className="primary" disabled={busy || !role}>
            {busy ? <Busy text="Updating and checking…" /> : "Save and recheck"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}

function Upload({
  projectId,
  close,
  done,
}: {
  projectId: string;
  close: () => void;
  done: () => Promise<void>;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [role, setRole] = useState("auto");
  const [revision, setRevision] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const [uploaded, setUploaded] = useState<string[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const add = (list: FileList | null) => {
    if (!list) return;
    setFiles((current) => [
      ...current,
      ...Array.from(list).filter(
        (f) => !current.some((c) => c.name === f.name && c.size === f.size),
      ),
    ]);
  };
  return (
    <Modal
      title="Add order evidence"
      eyebrow="Source documents"
      onClose={() => {
        if (!busy) close();
      }}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (!files.length) return;
          setBusy(true);
          setError("");
          try {
            for (const file of files) {
              if (uploaded.includes(file.name)) continue;
              if (file.size > 20 * 1024 * 1024)
                throw new Error(`${file.name} exceeds the 20 MB limit.`);
              const form = new FormData();
              form.append("file", file);
              form.append("kind", role);
              if (revision.trim())
                form.append("revision_label", revision.trim());
              await api(`/projects/${projectId}/documents`, form);
              setUploaded((v) => [...v, file.name]);
            }
            await done();
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <input
          ref={input}
          type="file"
          multiple
          accept=".pdf,.xlsx,.xls,.csv,.txt,.md"
          hidden
          onChange={(e) => add(e.target.files)}
        />
        <button
          type="button"
          className={`ow-upload-zone ${drag ? "dragging" : ""}`}
          onClick={() => input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            add(e.dataTransfer.files);
          }}
        >
          <UploadSimple size={28} weight="thin" />
          <strong>Drop files here, or browse</strong>
          <span>PDF drawings, specifications, markups, Excel and text</span>
        </button>
        {files.length > 0 && (
          <div className="ow-upload-files">
            {files.map((file) => (
              <div key={`${file.name}-${file.size}`}>
                <FileText size={15} />
                <span>{file.name}</span>
                {uploaded.includes(file.name) ? (
                  <CheckCircle size={15} />
                ) : (
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Remove ${file.name}`}
                    onClick={() => setFiles((v) => v.filter((f) => f !== file))}
                    disabled={busy}
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        <details className="ow-upload-options">
          <summary>
            Document details <span>Optional</span>
          </summary>
          <label>
            Document role
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="auto">Detect from document</option>
              <option value="specification">Specification</option>
              <option value="purchase_order">Purchase order</option>
              <option value="approval_drawing">Approval drawing</option>
              <option value="markups">Engineer’s markups</option>
              <option value="accepted_exception">Accepted exception</option>
              <option value="bom">Bill of materials</option>
              <option value="supplier_po">Supplier purchase order</option>
              <option value="nameplate">Nameplate</option>
            </select>
          </label>
          <label>
            Revision label
            <input
              placeholder="For example, Rev C"
              value={revision}
              onChange={(e) => setRevision(e.target.value)}
            />
          </label>
        </details>
        <p className="ow-upload-note">
          Rivet keeps the original, extracts cited facts, and rechecks the
          order. Unsupported or unreadable evidence remains visible as a gap.
        </p>
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <button
            type="button"
            className="secondary"
            onClick={close}
            disabled={busy}
          >
            Cancel
          </button>
          <button className="primary" disabled={busy || !files.length}>
            {busy ? (
              <Busy
                text={`Adding ${files.length} ${files.length === 1 ? "file" : "files"}…`}
              />
            ) : (
              "Add and check"
            )}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
function SignOff({
  title,
  eyebrow,
  description,
  action,
  version,
  onClose,
  onSubmit,
}: {
  title: string;
  eyebrow: string;
  description: string;
  action: string;
  version: number;
  onClose: () => void;
  onSubmit: (
    signer: string,
    reason: string,
    version: number,
  ) => Promise<boolean>;
}) {
  const [signer, setSigner] = useState("");
  const [reason, setReason] = useState("");
  const [expectedVersion] = useState(version);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal
      title={title}
      eyebrow={eyebrow}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <p className="ow-sign-description">{description}</p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            const okay = await onSubmit(
              signer.trim(),
              reason.trim(),
              expectedVersion,
            );
            if (!okay)
              setError(
                "The order could not be signed. Close this dialog to review the current status and any new evidence.",
              );
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          Your full name
          <input
            autoComplete="name"
            value={signer}
            onChange={(e) => setSigner(e.target.value)}
            required
            minLength={2}
            placeholder="Name of the responsible approver"
          />
        </label>
        <label>
          Reason for approval
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            required
            minLength={8}
            rows={4}
            placeholder="Record the basis for this decision…"
          />
        </label>
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <button
            className="secondary"
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="primary"
            disabled={
              busy || signer.trim().length < 2 || reason.trim().length < 8
            }
          >
            {busy ? (
              <Busy text="Recording signature…" />
            ) : (
              <>
                <ShieldCheck size={16} />
                {action}
              </>
            )}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
