import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  CircleNotch,
  DotsThree,
  FileText,
  PencilSimple,
  X,
} from "@phosphor-icons/react";
import Markdown from "react-markdown";
import { api, when } from "./api";
import { useAccount } from "./Auth";
import { Busy, ErrorNote, RivetIcon } from "./ui";
import type { ProjectFlow } from "./project-flow";
import type { RecordView } from "./record-types";
import type {
  RecordAgentRun,
  RecordAgentAnswer,
  RecordConversation,
} from "./record-agent-types";
import type { LifecycleView } from "./lifecycle-work";
import type { ActionExecutionView } from "./action-execution";
import { WorkDrawer } from "./WorkDrawer";
import {
  DecisionDetails,
  SourceDetails,
  StoryDetails,
  ReplyAction,
} from "./WorkDetails";
import { ReviewOutputs } from "./RevisionReview";
import { EquipmentEntry, EquipmentReview } from "./EquipmentReview";
import { EquipmentCorrections } from "./EquipmentCorrections";
import { DrawingRedesignReview } from "./DrawingRedesignReview";
import type { DrawingRedesignView } from "./drawing-redesign-types";
import {
  decisionItems,
  liveWork,
  orderStory,
  pmSentence,
  type ActivityEvent,
  type StoryEvent,
  type DecisionItem,
} from "./work-presentation";
import "./today-story.css";
import { orderOutcome, replyPreview } from "./workspace-outcomes";
import {
  orderReviewFromRoute,
  orderReviewLabel,
  orderReviewRoute,
  drawingReviewNotice,
  outlookReplyStatus,
  type OrderReviewView,
} from "./order-flow";
import "./workspace-refinement.css";
import "./order-flow.css";
import { ChangeOrderPanel } from "./CommentImpact";
import { visibleProjectComments } from "./project-records";
import {
  NeedsYouPanel,
  CorrectedFiles,
  OrderStatusLine,
  RevisionCheck,
  TitleBlock,
  TwoToneTitle,
  useEquipmentView,
} from "./OrderPanels";

type Drawer =
  | { kind: "event"; event: StoryEvent }
  | { kind: "source"; id: string; page?: number | null }
  | { kind: "decision"; item: DecisionItem }
  | { kind: "options" }
  | { kind: "equipment" }
  | { kind: "answer"; question: string; answer: RecordAgentAnswer };
type ProgressRun = RecordAgentRun & {
  comment_progress?: {
    total: number;
    prepared: number;
    failed: number;
    working: number;
  } | null;
};
export function OrderStory({
  id,
  navigate,
  notify,
}: {
  id: string;
  navigate: (to: string) => void;
  notify: (s: string) => void;
}) {
  const qc = useQueryClient(),
    account = useAccount();
  const q = useQuery({
    queryKey: ["project-work", id],
    queryFn: () => api<ProjectFlow>(`/project-work/${id}`),
    refetchInterval: 5000,
  });
  const activity = useQuery({
    queryKey: ["order-activity", id],
    queryFn: () => api<{ items: ActivityEvent[] }>(`/orders/${id}/timeline`),
    refetchInterval: 8000,
  });
  const record = useQuery({
    queryKey: ["record", id],
    queryFn: () => api<RecordView>(`/orders/${id}/record`),
    refetchInterval: 15000,
  });
  const conversation = useQuery({
    queryKey: ["record-conversation", id],
    queryFn: () => api<RecordConversation>(`/orders/${id}/record/conversation`),
    refetchOnWindowFocus: true,
  });
  const lifecycle = useQuery({
    queryKey: ["order-lifecycle", id],
    queryFn: () => api<LifecycleView>(`/orders/${id}/lifecycle`),
    refetchInterval: 10000,
  });
  const agent = useQuery({
    queryKey: ["record-agent-runs", id],
    queryFn: () =>
      api<{ runs: ProgressRun[] }>(`/orders/${id}/record/agent/runs`),
    refetchInterval: (query) =>
      query.state.data?.runs.some((r) =>
        ["queued", "running"].includes(r.status),
      )
        ? 2500
        : 10000,
  });
  const execution = useQuery({
    queryKey: ["workflow-actions", id],
    queryFn: () => api<ActionExecutionView>(`/orders/${id}/workflow-actions`),
    refetchInterval: 12000,
  });
  const drawingReview = useQuery({
    queryKey: ["drawing-redesign", id],
    queryFn: () =>
      api<DrawingRedesignView>(`/orders/${id}/engineering/redesign`),
    enabled: !!id,
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.latest &&
      ["queued", "running"].includes(query.state.data.latest.status)
        ? 2500
        : false,
  });
  const [drawer, setDrawer] = useState<Drawer | null>(null),
    [editing, setEditing] = useState(false),
    [name, setName] = useState(""),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const [question, setQuestion] = useState(""),
    [asking, setAsking] = useState(false),
    [askError, setAskError] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const equipment = useEquipmentView(id);
  const p = q.data,
    focus = new URLSearchParams(location.hash.split("?")[1] || "").get("event");
  const reviewView = orderReviewFromRoute(location.hash.slice(1));
  const story = p
    ? orderStory(
        p,
        activity.data?.items || [],
        lifecycle.data?.packages || [],
        execution.data,
      )
    : [];
  const focusedEvent = useRef("");
  useEffect(() => {
    if (focus && activity.data && focusedEvent.current !== focus) {
      const target = document.getElementById(`story-${focus}`);
      if (target) {
        target.scrollIntoView({ block: "center", behavior: "smooth" });
        focusedEvent.current = focus;
      }
    }
  }, [focus, activity.isSuccess, p?.id, story.length]);
  async function rename(e: React.FormEvent) {
    e.preventDefault();
    if (!p) return;
    setSaving(true);
    setError("");
    try {
      await api(
        `/project-work/${id}/name`,
        { name: name.trim(), expected_version: p.name_version },
        "PATCH",
      );
      await qc.invalidateQueries({ queryKey: ["project-work"] });
      setEditing(false);
      notify("Order name updated");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || asking) return;
    setAsking(true);
    setAskError("");
    const text = question.trim();
    try {
      const answer = await api<RecordAgentAnswer>(`/orders/${id}/record/ask`, {
        question: text,
        history: [],
      });
      setDrawer({ kind: "answer", question: text, answer });
      setQuestion("");
      void qc.invalidateQueries({ queryKey: ["record-conversation", id] });
    } catch (e) {
      setAskError((e as Error).message);
    } finally {
      setAsking(false);
    }
  }
  if (!p)
    return (
      <main className="page order-story-page">
        {q.isError ? (
          <ErrorNote message="This order could not load. Try again shortly." />
        ) : (
          <Busy text="Opening order…" />
        )}
      </main>
    );
  const progress =
      liveWork(agent.data?.runs || []) ||
      (p.status === "handling"
        ? {
            text: `Rivet is working: ${p.progress.find((s) => s.state === "working")?.label || "preparing the order response"}…`,
            total: 0,
            completed: 0,
          }
        : null),
    decisions = decisionItems(lifecycle.data?.packages || [], {
      ...p,
      version: lifecycle.data?.version,
    });
  const revision =
    (p.equipment_correction?.current !== false &&
      p.equipment_correction?.target_revision) ||
    record.data?.revision_review?.latest_revision;
  const outcome = orderOutcome(p),
    preview = replyPreview(p),
    latestReply = [...story].reverse().find((e) => e.reply);
  const drawingNotice = drawingReviewNotice(drawingReview.data?.latest);
  const visibleStory =
    !showHistory && !focus && story.length > 8 ? story.slice(-8) : story;
  const allDocuments = record.data?.documents || [];
  const inputDocuments = allDocuments.filter(
    (doc) =>
      !!doc.email?.subject ||
      /markup|mark-up|redline|review comments|customer comments/i.test(
        `${doc.name} ${doc.role}`,
      ),
  );
  const fallbackInputs = allDocuments.filter(
    (doc) => !/\.dxf$|bom|corrected|response|review package/i.test(doc.name),
  );
  const recentDocuments = (
    inputDocuments.length ? inputDocuments : fallbackInputs
  )
    .slice(-6)
    .reverse();
  const extractedComments = record.data
    ? visibleProjectComments(record.data, "", "all").filter(
        (comment) =>
          record.data?.response_log?.comments[comment.id]?.status !==
          "correspondence",
      )
    : [];
  const replyStatus = outlookReplyStatus({
    lastDirection: p.last_communication?.direction,
    nativeDraft: p.outlook.native,
    threadUrl: p.outlook.url,
    waitingForCustomer: p.status === "waiting_customer",
    lastSentAt: p.last_sent_at,
  });
  function selectReview(view: OrderReviewView) {
    navigate(orderReviewRoute(id, view));
  }
  const savedAnswer =
    conversation.data?.order_id === id
      ? conversation.data.messages.at(-1)
      : undefined;
  function sourceLabel(sourceId: string) {
    const source = record.data?.sources.find((s) => s.id === sourceId);
    const doc = record.data?.documents.find(
      (d) => d.id === source?.document_id,
    );
    return doc
      ? `${pmSentence(doc.name, "Source")}${source?.location.page ? ` · p. ${source.location.page}` : ""}`
      : "Source";
  }
  function chips(e: StoryEvent) {
    const ids =
      e.sourceIds ||
      (e.raw?.kind === "work"
        ? p?.reply_preparation?.prepared_reply?.source_refs.map((s) => s.id)
        : []) ||
      [];
    const unique = [
      ...new Map(ids.map((id) => [sourceLabel(id), id])).values(),
    ].slice(0, 3);
    return unique.length ? (
      <div className="story-source-chips">
        {unique.map((s) => (
          <button key={s} onClick={() => setDrawer({ kind: "source", id: s })}>
            <FileText size={13} />
            {sourceLabel(s)}
          </button>
        ))}
      </div>
    ) : null;
  }
  return (
    <main className="page order-story-page">
      <button
        className="text-button story-back"
        onClick={() => navigate("today")}
      >
        <ArrowLeft size={15} />
        Today
      </button>
      <header className="order-story-heading">
        <div>
          <span className="story-overline">
            {p.number}
            {p.sample ? " · Sample order" : ""}
          </span>
          {editing ? (
            <form className="story-rename-form" onSubmit={rename}>
              <input
                autoFocus
                aria-label="Order name"
                maxLength={180}
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <button className="primary" disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                className="story-icon-button"
                aria-label="Cancel rename"
                onClick={() => setEditing(false)}
              >
                <X size={18} />
              </button>
            </form>
          ) : (
            <div className="story-name">
              <h1>
                <TwoToneTitle name={p.name} />
              </h1>
              {["org:admin", "org:member"].includes(account.role) && (
                <button
                  className="story-icon-button"
                  aria-label="Rename order"
                  onClick={() => {
                    setName(p.name);
                    setEditing(true);
                  }}
                >
                  <PencilSimple size={17} />
                </button>
              )}
            </div>
          )}
          <p>
            {p.customer || "Customer not linked"}
            {revision
              ? ` · ${p.equipment_correction?.target_revision === revision ? `Draft revision ${revision}` : /public|unspecified/i.test(revision) ? revision : `Revision ${revision}`}`
              : ""}
          </p>
        </div>
        <div className="story-header-actions">
          <button
            className="story-icon-button"
            aria-label="Order options"
            onClick={() => setDrawer({ kind: "options" })}
          >
            <DotsThree size={24} />
          </button>
        </div>
      </header>
      {error && <ErrorNote message={error} />}
      {(q.isError ||
        activity.isError ||
        lifecycle.isError ||
        agent.isError) && (
        <ErrorNote message="Some order updates are delayed. Showing the last saved work." />
      )}
      {progress && (
        <section
          className="story-live"
          aria-label="Rivet’s live progress"
          role="status"
        >
          <CircleNotch size={20} className="project-spinning" />
          <div>
            <strong>{progress.text}</strong>
            {progress.total > 0 && (
              <progress
                aria-label="Comments prepared"
                value={progress.completed}
                max={progress.total}
              />
            )}
          </div>
        </section>
      )}
      <NeedsYouPanel
        order={p}
        decisions={decisions}
        seeWhy={(item) => setDrawer({ kind: "decision", item })}
        openReview={() => setDrawer({ kind: "equipment" })}
        openComments={() => navigate(`order/${id}?tab=comments`)}
      />
      <section className="order-review-workbench" aria-label="Order review">
        <div className="order-review-status">
          <span className="order-status-copy" role="status">
            <span
              className={`order-status-dot ${drawingNotice ? "needs-input" : `is-${outcome.tone}`}`}
            />
            {drawingNotice &&
            drawingReview.data?.latest?.current &&
            drawingReview.data.latest.totals.needs_information
              ? `${drawingReview.data.latest.totals.needs_information} requests need engineering input`
              : drawingNotice || outcome.label}
          </span>
          {drawingNotice && reviewView !== "changes" && (
            <button
              className="order-status-action"
              onClick={() => selectReview("changes")}
            >
              View checks <ArrowUpRight size={14} />
            </button>
          )}
        </div>
        <nav className="order-review-tabs" aria-label="Order workbench">
          {(["requests", "changes", "reply"] as const).map((view) => (
            <button
              type="button"
              key={view}
              className={reviewView === view ? "is-active" : ""}
              aria-current={reviewView === view ? "page" : undefined}
              onClick={() => selectReview(view)}
            >
              <span>{orderReviewLabel(view)}</span>
              {view === "reply" && (
                <span
                  className={`reply-tab-dot${p.outlook.native ? " is-ready" : ""}`}
                  aria-label={replyStatus}
                  title={replyStatus}
                />
              )}
            </button>
          ))}
        </nav>
        {reviewView === "requests" && (
          <div className="order-review-panel">
            {!!recentDocuments.length && (
              <div
                className="order-review-sources"
                aria-label="Original source files"
              >
                {recentDocuments.map((doc) => (
                  <button
                    key={doc.id}
                    title={doc.email?.subject || doc.name}
                    aria-label={`Open ${doc.email?.subject || doc.name}`}
                    onClick={() => setDrawer({ kind: "source", id: doc.id })}
                  >
                    <FileText size={16} />
                    <span>
                      {doc.email?.subject ||
                        (doc.name.length > 54
                          ? /spec/i.test(doc.name)
                            ? "Specification"
                            : /\.pdf$/i.test(doc.name)
                              ? "Returned drawings"
                              : "Source file"
                          : doc.name)}
                    </span>
                    <ArrowUpRight size={14} />
                  </button>
                ))}
                <button
                  className="order-view-all-files"
                  onClick={() => navigate(`order/${id}?tab=documents`)}
                >
                  View all files
                </button>
              </div>
            )}
            {!drawingReview.data?.latest?.comments.length && (
              <section
                className="order-markup-register"
                aria-label="Extracted customer requests"
              >
                <div className="order-markup-heading">
                  <strong>Customer markup</strong>
                  <span>
                    {extractedComments.length} requests in the order record
                  </span>
                </div>
                {extractedComments.length ? (
                  <ol>
                    {extractedComments.slice(0, 6).map((comment) =>
                      (() => {
                        const logEntry =
                          record.data?.response_log?.comments[comment.id];
                        const response = comment.response || logEntry?.response;
                        return (
                          <li key={comment.id}>
                            <div className="order-request-copy">
                              <p>{comment.text || comment.original_text}</p>
                              <span>
                                {logEntry?.label ||
                                  comment.workflow_status?.replaceAll(
                                    "_",
                                    " ",
                                  ) ||
                                  comment.status}
                              </span>
                              {response && (
                                <details className="order-request-response">
                                  <summary>Response and findings</summary>
                                  <blockquote>{response}</blockquote>
                                </details>
                              )}
                            </div>
                            <button
                              onClick={() =>
                                setDrawer({
                                  kind: "source",
                                  id:
                                    comment.source_ids[0] ||
                                    comment.document_id,
                                })
                              }
                            >
                              Open source <ArrowUpRight size={13} />
                            </button>
                          </li>
                        );
                      })(),
                    )}
                  </ol>
                ) : (
                  <p className="order-markup-empty">
                    No extracted requests are recorded yet. Open the markup log
                    to review the source files and add or inspect comments.
                  </p>
                )}
                <button
                  className="order-markup-log"
                  onClick={() => navigate(`order/${id}?tab=comments`)}
                >
                  {extractedComments.length > 6
                    ? `View all ${extractedComments.length} requests`
                    : "Open full markup log"}
                  <ArrowUpRight size={14} />
                </button>
              </section>
            )}
            <DrawingRedesignReview
              id={id}
              initialOpen
              screen="requests"
              onOpenSource={(documentId, page) =>
                setDrawer({ kind: "source", id: documentId, page })
              }
              onViewChanges={(changeId) =>
                navigate(
                  `${orderReviewRoute(id, "changes")}&change=${encodeURIComponent(changeId)}`,
                )
              }
            />
            {!!drawingReview.data?.latest?.comments.length && (
              <button
                className="order-markup-log"
                onClick={() => navigate(`order/${id}?tab=comments`)}
              >
                Comment log and exports <ArrowUpRight size={14} />
              </button>
            )}
          </div>
        )}
        {reviewView === "changes" && (
          <div className="order-review-panel">
            {!drawingReview.data?.latest && (
              <div className="order-review-panel-heading">
                <div>
                  <h2>Files & changes</h2>
                  <p>Compare revisions. Download checked files.</p>
                </div>
              </div>
            )}
            <CorrectedFiles order={p} />
            <RevisionCheck
              order={p}
              openReview={() => setDrawer({ kind: "equipment" })}
            />
            {(!p.equipment_correction ||
              p.equipment_correction.current === false) &&
              !equipment.data?.comparison?.checks.length &&
              !drawingReview.data?.latest && (
                <EquipmentEntry
                  id={id}
                  open={() => setDrawer({ kind: "equipment" })}
                />
              )}
            <DrawingRedesignReview id={id} initialOpen screen="changes" />
          </div>
        )}
        {reviewView === "reply" && (
          <section
            className={`order-outcome is-${outcome.tone}`}
            aria-label="Outlook reply status"
          >
            <div className="order-outcome-symbol">
              <RivetIcon
                name={
                  outcome.tone === "ready"
                    ? "handoff"
                    : outcome.tone === "working"
                      ? "agent"
                      : "review"
                }
                size={25}
              />
            </div>
            <div className="order-outcome-copy">
              <span className={`work-state is-${outcome.tone}`}>
                <span />
                {outcome.label}
              </span>
              <h2>{outcome.title}</h2>
              <p>{pmSentence(p.explanation, outcome.detail)}</p>
              {preview && <blockquote>{preview}</blockquote>}
              {latestReply && (
                <button
                  className="text-button"
                  onClick={() =>
                    setDrawer({ kind: "event", event: latestReply })
                  }
                >
                  Read prepared response
                </button>
              )}
            </div>
            <div className="order-outcome-actions">
              {["ready", "prepared"].includes(outcome.tone) ? (
                <ReplyAction
                  order={p}
                  label={
                    p.outlook.native
                      ? "Open reply in Outlook"
                      : "Open email thread"
                  }
                />
              ) : null}
              {p.outlook.url &&
                !["ready", "prepared"].includes(outcome.tone) && (
                  <a
                    className="text-button"
                    href={p.outlook.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open in Outlook <ArrowUpRight size={14} />
                  </a>
                )}
            </div>
          </section>
        )}
      </section>
      <details className="order-more-tools" open={!!focus}>
        <summary>More tools and order details</summary>
        <div className="order-more-tools-content">
          <div className="order-more-tool-links">
            <button onClick={() => navigate(`order/${id}?stage=requirements`)}>
              Engineering tools <ArrowUpRight size={14} />
            </button>
            <button
              onClick={() => navigate(`order/${id}?view=tools&tab=comments`)}
            >
              Drawing and comments <ArrowUpRight size={14} />
            </button>
          </div>
          <TitleBlock order={p} revision={revision} />
          <div className="order-workspace-grid">
            <div className="order-workspace-main">
              <ChangeOrderPanel
                summary={record.data?.comment_impact}
                openComment={(commentId) =>
                  navigate(
                    `order/${id}?view=tools&tab=comments&comment=${commentId}`,
                  )
                }
              />
              <RevisionCheck
                order={p}
                openReview={() => setDrawer({ kind: "equipment" })}
              />
              {outcome.tone !== "attention" && !decisions.length && (
                <section
                  className={`order-outcome is-${outcome.tone}`}
                  aria-label="Current order outcome"
                >
                  <div className="order-outcome-symbol">
                    <RivetIcon
                      name={
                        outcome.tone === "ready"
                          ? "handoff"
                          : outcome.tone === "working"
                            ? "agent"
                            : "review"
                      }
                      size={25}
                    />
                  </div>
                  <div className="order-outcome-copy">
                    <span className={`work-state is-${outcome.tone}`}>
                      <span />
                      {outcome.label}
                    </span>
                    {["ready", "prepared"].includes(outcome.tone) && (
                      <span className="rivet-chip">Prepared by Rivet</span>
                    )}
                    <h2>{outcome.title}</h2>
                    <p>{pmSentence(p.explanation, outcome.detail)}</p>
                    {preview && ["ready", "prepared"].includes(p.status) && (
                      <blockquote>{preview}</blockquote>
                    )}
                    {["ready", "prepared"].includes(p.status) && (
                      <CorrectedFiles order={p} />
                    )}
                  </div>
                  <div className="order-outcome-actions">
                    {outcome.tone === "ready" ? (
                      <span className="rivet-output">
                        <ReplyAction order={p} />
                      </span>
                    ) : latestReply && outcome.tone === "prepared" ? (
                      <button
                        className="primary"
                        onClick={() =>
                          setDrawer({ kind: "event", event: latestReply })
                        }
                      >
                        Read response
                      </button>
                    ) : null}
                    {latestReply && outcome.tone === "ready" && (
                      <button
                        className="text-button"
                        onClick={() =>
                          setDrawer({ kind: "event", event: latestReply })
                        }
                      >
                        {p.equipment_correction
                          ? "View corrected files"
                          : "Read response here"}
                      </button>
                    )}
                  </div>
                </section>
              )}
              {(!p.equipment_correction ||
                p.equipment_correction.current === false) &&
                !equipment.data?.comparison?.checks.length && (
                  <EquipmentEntry
                    id={id}
                    open={() => setDrawer({ kind: "equipment" })}
                  />
                )}
              <div className="story-section-heading">
                <h2>Order timeline</h2>
                <span>
                  {p.document_count} {p.document_count === 1 ? "file" : "files"}{" "}
                  · {p.email_count} {p.email_count === 1 ? "email" : "emails"}
                </span>
              </div>
              {!showHistory && !focus && story.length > 8 && (
                <button
                  className="story-earlier"
                  onClick={() => setShowHistory(true)}
                >
                  Show {story.length - 8} earlier updates
                </button>
              )}
              {activity.isPending ? (
                <div className="today-skeleton">
                  <div />
                  <div />
                </div>
              ) : (
                <ol className="order-story-timeline" aria-label="Order story">
                  {visibleStory.map((e, i) => (
                    <li
                      id={`story-${e.id}`}
                      className={`story-event is-${e.state}${focus === e.id || e.relatedIds?.includes(focus || "") ? " is-focused" : ""}`}
                      key={e.id}
                    >
                      {e.relatedIds?.map((id) => (
                        <span
                          className="story-event-anchor"
                          key={id}
                          id={`story-${id}`}
                          aria-hidden="true"
                        />
                      ))}
                      <span className="story-event-dot" aria-label={e.state}>
                        {e.state === "done" ? <Check size={12} /> : null}
                      </span>
                      <div className="story-event-body">
                        {e.state === "upcoming" ? (
                          <p className="story-upcoming">{e.title}</p>
                        ) : (
                          <>
                            <button
                              className="story-event-title"
                              onClick={() =>
                                setDrawer({ kind: "event", event: e })
                              }
                            >
                              {e.title}
                            </button>
                            <div className="story-event-meta">
                              {e.at && (
                                <time dateTime={e.at}>{when(e.at)}</time>
                              )}
                              {e.subtitle && (
                                <span>
                                  {pmSentence(e.subtitle, "Order update")}
                                </span>
                              )}
                            </div>
                            {chips(e)}
                            {e.reply && e.state === "current" && (
                              <div className="story-reply-actions">
                                {p.outlook.native ? (
                                  <>
                                    <ReplyAction order={p} />
                                    <button
                                      className="text-button"
                                      onClick={() =>
                                        setDrawer({ kind: "event", event: e })
                                      }
                                    >
                                      {p.equipment_correction
                                        ? "View corrected files"
                                        : "Read response here"}
                                    </button>
                                  </>
                                ) : (
                                  <button
                                    className="primary"
                                    onClick={() =>
                                      setDrawer({ kind: "event", event: e })
                                    }
                                  >
                                    Review response
                                  </button>
                                )}
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </div>
            <aside className="order-context-rail" aria-label="Order context">
              <section className="order-properties">
                <span className="section-eyebrow">Properties</span>
                <dl>
                  <dt>Status</dt>
                  <dd>
                    <OrderStatusLine order={p} />
                  </dd>
                  <dt>Job</dt>
                  <dd className="mono">{p.number}</dd>
                  <dt>Customer</dt>
                  <dd>{p.customer || "Not linked"}</dd>
                  <dt>Revision</dt>
                  <dd>
                    {p.equipment_correction?.target_revision === revision
                      ? `${revision} · draft`
                      : revision || "Not recorded"}
                  </dd>
                  <dt>Source files</dt>
                  <dd>{p.document_count}</dd>
                  <dt>Conversations</dt>
                  <dd>{p.thread_count}</dd>
                </dl>
              </section>
              {p.last_communication && (
                <section className="order-last-message">
                  <span className="section-eyebrow">Last communication</span>
                  <strong>{p.last_communication.subject}</strong>
                  <p>
                    {p.last_communication.direction === "sent"
                      ? "Sent"
                      : "Received"}{" "}
                    · {when(p.last_communication.at)}
                  </p>
                  {p.last_communication.outlook_url && (
                    <a
                      href={p.last_communication.outlook_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open in Outlook <ArrowUpRight size={13} />
                    </a>
                  )}
                </section>
              )}
              {!!recentDocuments.length && (
                <section className="order-source-list">
                  <span className="section-eyebrow">Recent source files</span>
                  {recentDocuments.map((doc) => (
                    <button
                      key={doc.id}
                      onClick={() => setDrawer({ kind: "source", id: doc.id })}
                    >
                      <FileText size={16} />
                      <span>{doc.name}</span>
                    </button>
                  ))}
                  <button
                    className="text-button"
                    onClick={() =>
                      navigate(`order/${id}?view=tools&tab=documents`)
                    }
                  >
                    All files and evidence
                  </button>
                </section>
              )}
            </aside>
          </div>
          <div className="story-ask-dock">
            <form onSubmit={ask}>
              <RivetIcon name="agent" size={21} />
              <input
                aria-label="Ask Rivet about this order"
                placeholder="Ask Rivet about this order…"
                value={question}
                maxLength={3000}
                onChange={(e) => setQuestion(e.target.value)}
                disabled={asking}
              />
              <button disabled={asking || !question.trim()} type="submit">
                {asking ? "Looking…" : "Ask Rivet"}
              </button>
              {savedAnswer && (
                <button
                  type="button"
                  onClick={() =>
                    setDrawer({
                      kind: "answer",
                      question: savedAnswer.question,
                      answer: savedAnswer.result,
                    })
                  }
                >
                  Saved answer
                </button>
              )}
            </form>
            {askError && <ErrorNote message={askError} />}
          </div>
        </div>
      </details>
      {drawer && (
        <WorkDrawer
          title={
            drawer.kind === "source"
              ? "Source"
              : drawer.kind === "equipment"
                ? "Drawing and BOM check"
                : drawer.kind === "options"
                  ? "Order options"
                  : drawer.kind === "answer"
                    ? "Rivet’s answer"
                    : drawer.kind === "decision"
                      ? drawer.item.title
                      : drawer.event.reply
                        ? p.equipment_correction
                          ? "Corrected drawing and BOM"
                          : "Rivet’s prepared response"
                        : drawer.event.title
          }
          eyebrow={p.number}
          close={() => setDrawer(null)}
        >
          {drawer.kind === "equipment" ? (
            <EquipmentReview id={id} />
          ) : drawer.kind === "source" ? (
            <SourceDetails
              orderId={id}
              sourceId={drawer.id}
              page={drawer.page}
              close={() => setDrawer(null)}
            />
          ) : drawer.kind === "event" &&
            drawer.event.reply &&
            p.equipment_correction ? (
            <EquipmentCorrections id={id} />
          ) : drawer.kind === "event" ? (
            <StoryDetails
              event={drawer.event}
              order={p}
              openSource={(id) => setDrawer({ kind: "source", id })}
            />
          ) : drawer.kind === "decision" ? (
            <DecisionDetails
              item={drawer.item}
              navigate={navigate}
              openSource={(id) => setDrawer({ kind: "source", id })}
            />
          ) : drawer.kind === "options" ? (
            <>
              <h3>Downloads</h3>
              <ReviewOutputs id={id} number={p.number} includeBrief={false} />
              <h3>Order tools</h3>
              <div className="drawer-options">
                <button
                  onClick={() =>
                    navigate(`order/${id}?view=tools&tab=documents`)
                  }
                >
                  Files and connected sources <ArrowUpRight size={15} />
                </button>
                <button
                  onClick={() => navigate(`order/${id}?stage=requirements`)}
                >
                  Engineering tools <ArrowUpRight size={15} />
                </button>
                <button
                  onClick={() =>
                    navigate(`order/${id}?view=advanced&tab=sharing`)
                  }
                >
                  Approvers and order settings <ArrowUpRight size={15} />
                </button>
                <button onClick={() => navigate("activity?order=" + id)}>
                  Full activity history <ArrowUpRight size={15} />
                </button>
              </div>
              <p className="drawer-footnote">
                {p.communication_sync === "delayed"
                  ? "Outlook history is updating. Previously confirmed messages are shown."
                  : p.communication_sync === "ambiguous"
                    ? "One email mentions several jobs. Automatic filing is paused for that conversation."
                    : p.email_count
                      ? "Email history is connected to Outlook."
                      : "This order uses uploaded documents. No Outlook conversation is linked."}
              </p>
            </>
          ) : (
            <>
              <p className="drawer-intro">{drawer.question}</p>
              <div className="drawer-reading">
                <Markdown disallowedElements={["img"]}>
                  {drawer.answer.answer}
                </Markdown>
              </div>
              <div className="story-source-chips">
                {drawer.answer.source_ids.map((s) => (
                  <button
                    key={s}
                    onClick={() => setDrawer({ kind: "source", id: s })}
                  >
                    {sourceLabel(s)}
                    <ArrowUpRight size={13} />
                  </button>
                ))}
              </div>
            </>
          )}
        </WorkDrawer>
      )}
    </main>
  );
}
