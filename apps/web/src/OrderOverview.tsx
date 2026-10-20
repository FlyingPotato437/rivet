import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CaretDown,
  Check,
  FileText,
  Plus,
  X,
  CircleNotch,
} from "@phosphor-icons/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, when } from "./api";
import { useAccount } from "./Auth";
import { ActorName } from "./RecordAttribution";
import { OrderPreparedReply } from "./OrderPreparedReply";
import { LifecycleSummary, useOrderLifecycle } from "./LifecycleSummary";
import { OrderEvidence } from "./OrderEvidence";
import { ConnectedSources } from "./ConnectedSources";
import { CustomerBrief } from "./CustomerBrief";
import { RecordUpload } from "./RecordWorkspace";
import { ReviewOutputs } from "./RevisionReview";
import { resolveRecordSource } from "./record-source-navigation";
import { Busy, ErrorNote, Modal, RivetIcon } from "./ui";
import type { RecordView } from "./record-types";
import type {
  RecordAgentView,
  RecordAgentProposal,
} from "./record-agent-types";
import "./straightforward-work.css";

export function OrderOverview({
  id,
  navigate,
  notify,
}: {
  id: string;
  navigate: (to: string) => void;
  notify: (s: string) => void;
}) {
  const qc = useQueryClient();
  const account = useAccount();
  const query = useQuery({
    queryKey: ["record", id],
    queryFn: () => api<RecordView>(`/orders/${id}/record`),
    refetchInterval: 8000,
  });
  const agent = useQuery({
    queryKey: ["record-agent", id],
    queryFn: () => api<RecordAgentView>(`/orders/${id}/record/agent`),
    refetchInterval: 8000,
  });
  const w = query.data;
  const lifecycle = useOrderLifecycle(id);
  const hasLifecycle = !!lifecycle.data?.packages.length;
  const SavedWork = hasLifecycle ? "details" : "section";
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const [source, setSource] = useState(params.get("source") || "");
  const [selected, setSelected] = useState(params.get("comment") || "");
  const [upload, setUpload] = useState(false);
  const [asking, setAsking] = useState(false);
  const [request, setRequest] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState(false);
  useEffect(() => {
    setSource(params.get("source") || "");
    setSelected(params.get("comment") || "");
  }, [id, params.get("source"), params.get("comment")]);
  const sourceLink =
    w && source ? resolveRecordSource(w, source, selected) : null;
  const document = w?.documents.find((d) => d.id === sourceLink?.documentId);
  const currentRun = agent.data?.runs[0];
  const working = agent.data?.runs.find((run) =>
    ["queued", "running"].includes(run.status),
  );
  const prepared =
    agent.data?.proposals.filter(
      (p) => p.status === "prepared" && p.base_version === w?.version,
    ) || [];
  const pending =
    agent.data?.proposals.filter(
      (p) =>
        p.status === "pending" &&
        p.base_version === w?.version &&
        !agent.data?.runs.some(
          (run) =>
            run.id === p.run_id && ["queued", "running"].includes(run.status),
        ),
    ) || [];
  const question = agent.data?.runs.find(
    (run) =>
      run.status === "waiting_for_input" &&
      run.base_version === w?.version &&
      run.result.question,
  );
  const comment = w?.comments.find((c) => c.id === selected);
  const readyCount = prepared.reduce((n, p) => n + p.operations.length, 0);
  const savedCorrespondence =
    w?.notices.filter((n) => n.status === "draft" && n.body) || [];
  async function refresh() {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["record", id] }),
      qc.invalidateQueries({ queryKey: ["record-agent", id] }),
      qc.invalidateQueries({ queryKey: ["record-inbox"] }),
      qc.invalidateQueries({ queryKey: ["autopilot"] }),
      qc.invalidateQueries({ queryKey: ["records"] }),
    ]);
  }
  async function decide(p: RecordAgentProposal, action: "accept" | "reject") {
    if (!w) return;
    setBusy(true);
    setError("");
    try {
      await api(`/orders/${id}/record/agent/proposals/${p.id}/resolve`, {
        expected_version: w.version,
        action,
        actor: account.name,
        reason:
          action === "accept"
            ? "Checked the proposed record changes."
            : "This proposed record change is not needed.",
      });
      await refresh();
      notify(
        action === "accept" ? "Record changes saved" : "Proposal dismissed",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function task(answer = false) {
    if (!w || !request.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api(
        `/orders/${id}/record/agent/runs${answer && question ? `/${question.id}/resume` : ""}`,
        {
          expected_version: w.version,
          actor: account.name,
          reason: answer
            ? "Answered the blocking order question."
            : "Requested additional order work.",
          ...(answer ? { answer: request.trim() } : { goal: request.trim() }),
        },
      );
      setRequest("");
      setAsking(false);
      await refresh();
      notify("Rivet is working on it");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (!w)
    return (
      <main className="page order-overview">
        {query.isError ? (
          <ErrorNote message="Could not open the order. Try again." />
        ) : (
          <Busy text="Opening the order…" />
        )}
      </main>
    );
  const detectedRevision =
    w.revision_review?.latest_revision || w.comments.at(-1)?.revision;
  const latestRevision =
    detectedRevision && detectedRevision !== "Unspecified"
      ? detectedRevision
      : "";
  const returnedDocument = w.documents.find((d) =>
    ["markup", "returned_submittal", "review_comments"].includes(d.role),
  );
  const evidence = (sourceId: string) => {
    setSource(sourceId);
  };
  const events = [
    ...w.events.map((event) => ({
      id: event.id,
      at: event.at,
      title: event.summary,
      detail: event.reason,
      actor:
        event.actor_kind === "ai"
          ? "AI · Rivet"
          : event.actor_kind === "automatic"
            ? "Automatic"
            : event.actor,
    })),
    ...(agent.data?.runs || [])
      .filter((run) => run.status === "completed")
      .map((run) => ({
        id: run.id,
        at: run.updated_at,
        title:
          run.mode === "ai"
            ? "Rivet prepared the order work"
            : "Automatic source check completed",
        detail: run.result.summary || "",
        actor: run.mode === "ai" ? "AI · Rivet" : "Automatic",
      })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  return (
    <main className={`page order-overview${source ? " evidence-open" : ""}`}>
      <button
        className="text-button order-overview-back"
        onClick={() => navigate("work")}
      >
        <ArrowLeft size={16} />
        Your work
      </button>
      <header className="order-overview-heading">
        <div>
          <span className="work-eyebrow">
            {w.order.number}
            {w.order.synthetic ? " · Sample order" : ""}
          </span>
          <h1>{w.order.title}</h1>
          <p>
            {w.order.customer}
            {latestRevision && ` · Revision ${latestRevision}`}
          </p>
        </div>
        <button className="work-add-file" onClick={() => setUpload(true)}>
          <Plus size={17} />
          Add documents
        </button>
      </header>
      {error && <ErrorNote message={error} />}{" "}
      {query.isError && (
        <ErrorNote message="Could not refresh. Showing the last saved order." />
      )}
      <div className="order-overview-columns">
        <div className="order-overview-main">
          <div className="order-capture-summary">
            <RivetIcon name="drawing" size={24} />
            <div>
              <strong>
                {w.comments.length
                  ? `${w.comments.length} comments captured`
                  : `${w.documents.length} source ${w.documents.length === 1 ? "file" : "files"} available`}
              </strong>
              <p>
                {w.documents.length}{" "}
                {w.documents.length === 1 ? "file" : "files"} in the order
                record
                {latestRevision ? ` · latest return ${latestRevision}` : ""}
              </p>
            </div>
            {w.documents[0] && (
              <button
                className="text-button"
                onClick={() =>
                  evidence(returnedDocument?.id || w.documents[0].id)
                }
              >
                {returnedDocument ? "Open returned file" : "Open source file"}
              </button>
            )}
          </div>
          {!w.comments.length && <ConnectedSources orderId={id} compact />}
          <LifecycleSummary orderId={id} record={w} openSource={evidence} />
          {hasLifecycle ? (
            <details className="lifecycle-secondary-work">
              <summary>Mailbox response and delivery status</summary>
              <OrderPreparedReply orderId={id} expanded openSource={evidence} />
            </details>
          ) : (
            <OrderPreparedReply orderId={id} expanded openSource={evidence} />
          )}
          {working ? (
            <section className="order-working-line" aria-live="polite">
              <CircleNotch size={20} className="work-spinning" />
              <div>
                <strong>Rivet is preparing the supporting work</strong>
                <p>
                  {working.steps.at(-1)?.label || "Reading the order evidence."}
                </p>
              </div>
            </section>
          ) : (
            readyCount > 0 && (
              <SavedWork
                className={`order-finished-work${hasLifecycle ? " lifecycle-secondary-work" : ""}`}
              >
                {hasLifecycle && (
                  <summary>Additional recorded drafts · {readyCount}</summary>
                )}
                {!hasLifecycle && (
                  <header>
                    <RivetIcon name="agent" size={24} />
                    <div>
                      <h2>
                        {readyCount} {readyCount === 1 ? "draft" : "drafts"}{" "}
                        prepared
                      </h2>
                      <p>
                        Saved by Rivet ·{" "}
                        {prepared.some((p) => p.origin === "ai")
                          ? "AI prepared"
                          : "Rule-based preparation"}
                      </p>
                    </div>
                  </header>
                )}
                {prepared.flatMap((p) =>
                  p.operations.map((op, index) => (
                    <details
                      className="order-saved-draft"
                      key={`${p.id}:${index}`}
                    >
                      <summary>{op.title || "Prepared clarification"}</summary>
                      <div className="order-draft-text">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {op.text || ""}
                        </ReactMarkdown>
                      </div>
                      <div className="order-source-links">
                        {op.evidence_ids.map((sid) => (
                          <button key={sid} onClick={() => evidence(sid)}>
                            {sourceLabel(w, sid)}
                          </button>
                        ))}
                      </div>
                    </details>
                  )),
                )}
              </SavedWork>
            )
          )}
          {!!savedCorrespondence.length && (
            <details className="order-saved-correspondence">
              <summary>
                Saved correspondence · {savedCorrespondence.length}
              </summary>
              <p className="order-saved-note">
                Earlier drafts retained in the record. The prepared response
                above uses the current sources.
              </p>
              {savedCorrespondence.map((n) => (
                <article key={n.id}>
                  <h3>{n.title}</h3>
                  <span>
                    {n.origin === "ai"
                      ? "AI prepared"
                      : n.reviewed_by
                        ? `Reviewed by ${n.reviewed_by}`
                        : "Saved draft"}{" "}
                    · {when(n.at)}
                  </span>
                  <div className="order-draft-text">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {n.body}
                    </ReactMarkdown>
                  </div>
                  <div className="order-source-links">
                    {(n.source_ids || []).map((sid) => (
                      <button key={sid} onClick={() => evidence(sid)}>
                        {sourceLabel(w, sid)}
                      </button>
                    ))}
                  </div>
                </article>
              ))}
            </details>
          )}
          {question && (
            <section className="order-one-question">
              <h2>One detail is needed</h2>
              <p>{question.result.question}</p>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void task(true);
                }}
              >
                <input
                  aria-label="Answer Rivet’s question"
                  value={request}
                  onChange={(e) => setRequest(e.target.value)}
                  placeholder="Your answer"
                  required
                />
                <button className="primary" disabled={busy || !request.trim()}>
                  Continue
                </button>
              </form>
            </section>
          )}
          {pending.map((p) => (
            <section className="order-one-question" key={p.id}>
              <span className="work-eyebrow">
                {p.origin === "ai" ? "AI proposed" : "Automatic proposal"}
              </span>
              <h2>{p.title}</h2>
              <ul>
                {p.operations.map((op, index) => (
                  <li key={index}>
                    <strong>{op.title || op.type.replaceAll("_", " ")}</strong>
                    {op.text && <p>{op.text}</p>}
                  </li>
                ))}
              </ul>
              <details>
                <summary>Supporting evidence</summary>
                <div className="order-source-links">
                  {p.evidence_ids.map((sid) => (
                    <button key={sid} onClick={() => evidence(sid)}>
                      {sourceLabel(w, sid)}
                    </button>
                  ))}
                </div>
              </details>
              <div className="order-decision-actions">
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => void decide(p, "accept")}
                >
                  <Check size={16} />
                  Looks good · save
                </button>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => void decide(p, "reject")}
                >
                  Dismiss
                </button>
              </div>
              <small>
                Saves the listed record changes. Engineering and commercial
                approvals remain with your team.
              </small>
            </section>
          ))}
          {!working && currentRun?.status === "failed" && (
            <details className="order-technical-detail">
              <summary>Rivet could not finish one supporting task</summary>
              <p>{currentRun.result.error || currentRun.result.summary}</p>
              <button
                className="text-button"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await api(
                      `/orders/${id}/record/agent/runs/${currentRun.id}/retry`,
                      {
                        expected_version: w.version,
                        actor: account.name,
                        reason: "Retry the interrupted preparation.",
                      },
                    );
                    await refresh();
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Retry preparation
              </button>
            </details>
          )}
          <section className="order-correspondence">
            <div className="order-section-heading">
              <h2>Order record</h2>
              <span>
                {w.comments.length} comments · {w.documents.length}{" "}
                {w.documents.length === 1 ? "file" : "files"}
              </span>
            </div>
            <details
              className="order-comment-log"
              open={!!comment || undefined}
            >
              <summary>Comments and responses</summary>
              {w.comments.length ? (
                w.comments.map((c) => (
                  <article
                    className={`order-log-comment${selected === c.id ? " selected" : ""}`}
                    key={c.id}
                  >
                    <div>
                      <span>
                        Comment {c.number}
                        {c.revision && ` · Rev ${c.revision}`}
                      </span>
                      <small>
                        {c.status === "closed"
                          ? "Closed"
                          : c.response
                            ? "Response recorded"
                            : "Recorded"}
                      </small>
                    </div>
                    <p>{c.text}</p>
                    {c.response && (
                      <blockquote>
                        {c.response}
                        <small>{c.responder}</small>
                      </blockquote>
                    )}
                    <div className="order-source-links">
                      <button
                        onClick={() => {
                          setSelected(c.id);
                          evidence(c.source_ids[0] || c.document_id);
                        }}
                      >
                        Open source
                        {c.source_page ? ` · p. ${c.source_page}` : ""}
                      </button>
                      <button
                        onClick={() =>
                          navigate(
                            `order/${id}?view=tools&tab=work&comment=${c.id}&edit=1`,
                          )
                        }
                      >
                        Edit record
                      </button>
                    </div>
                  </article>
                ))
              ) : (
                <p>
                  Comments appear when Rivet reads a returned drawing or email.
                </p>
              )}
            </details>
            <details className="order-file-fold">
              <summary>Files and connected context</summary>
              <div className="order-overview-files">
                {w.documents.map((d) => (
                  <button key={d.id} onClick={() => evidence(d.id)}>
                    <FileText size={18} />
                    <span>
                      <strong>{d.email?.subject || d.name}</strong>
                      <small>
                        {d.email?.from || d.role.replaceAll("_", " ")}
                        {d.revision_label && ` · Rev ${d.revision_label}`}
                      </small>
                    </span>
                    <span>
                      {["queued", "processing", "parsing"].includes(d.state)
                        ? "Reading…"
                        : d.state === "failed"
                          ? "Could not read"
                          : "Open"}
                    </span>
                  </button>
                ))}
              </div>
              {!!w.comments.length && <ConnectedSources orderId={id} compact />}
              <CustomerBrief orderId={id} />
            </details>
          </section>
          <section className="order-history">
            <div className="order-section-heading">
              <h2>Activity history</h2>
              <button
                className="text-button"
                onClick={() => setHistory(!history)}
              >
                {history ? "Hide history" : "View history"}
              </button>
            </div>
            {history &&
              (events.length ? (
                <ol>
                  {events.map((event) => (
                    <li key={event.id}>
                      <div>
                        <strong>{event.title}</strong>
                        <span>
                          <ActorName actor={event.actor} /> · {when(event.at)}
                        </span>
                      </div>
                      {event.detail && (
                        <details>
                          <summary>Details</summary>
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {event.detail}
                          </ReactMarkdown>
                        </details>
                      )}
                    </li>
                  ))}
                </ol>
              ) : (
                <p>
                  New correspondence and recorded decisions will appear here.
                </p>
              ))}
          </section>
          <details className="order-extra-tools">
            <summary>Exports and order settings</summary>
            <ReviewOutputs id={id} number={w.order.number} />
            <div className="order-source-links">
              <button
                onClick={() => navigate(`order/${id}?view=tools&tab=sharing`)}
              >
                Approvals and sharing
              </button>
              <button
                onClick={() => navigate(`order/${id}?view=tools&tab=changes`)}
              >
                Compare revisions
              </button>
              <button
                onClick={() =>
                  navigate(`order/${id}?view=tools&tab=coordination`)
                }
              >
                Team assignments
              </button>
            </div>
          </details>
          <div className="order-ask-rivet">
            <button
              className="text-button"
              onClick={() => setAsking(!asking)}
              aria-expanded={asking}
            >
              <RivetIcon name="agent" size={18} />
              Ask Rivet about this order
              <CaretDown size={15} />
            </button>
            {asking && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void task();
                }}
              >
                <textarea
                  aria-label="Ask Rivet about this order"
                  value={request}
                  onChange={(e) => setRequest(e.target.value)}
                  placeholder="Ask a question or request additional work…"
                  rows={3}
                />
                <button
                  className="primary"
                  disabled={busy || !!working || !request.trim()}
                >
                  Ask Rivet
                </button>
              </form>
            )}
          </div>
        </div>
        {source && (
          <aside
            className="order-overview-evidence"
            aria-label="Source beside the order"
          >
            <header>
              <strong>
                {document?.email?.subject ||
                  document?.name ||
                  "Source evidence"}
              </strong>
              <button
                className="icon-button"
                aria-label="Close source"
                onClick={() => {
                  setSource("");
                  if (params.has("source")) navigate(`order/${id}`);
                }}
              >
                <X size={18} />
              </button>
            </header>
            {document ? (
              <OrderEvidence
                document={document}
                sources={w.sources.filter((s) => s.document_id === document.id)}
                selectedId={sourceLink?.sourceId}
                selectionRequest={1}
                inline
                workbench
                close={() => setSource("")}
              />
            ) : (
              <ErrorNote message="This source is not available in this order. No substitute was opened." />
            )}
          </aside>
        )}
      </div>
      {upload && (
        <RecordUpload
          w={w}
          close={() => setUpload(false)}
          done={() => {
            setUpload(false);
            void refresh();
            notify("Documents added. Rivet will prepare the work.");
          }}
        />
      )}
    </main>
  );
}
function sourceLabel(w: RecordView, id: string) {
  const source = w.sources.find((s) => s.id === id);
  const doc = w.documents.find((d) => d.id === (source?.document_id || id));
  return `${doc?.email?.subject || doc?.name || "Source"}${source?.location.page ? ` · p. ${source.location.page}` : ""}`;
}
