import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowClockwise,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCircle,
  ClipboardText,
  Clock,
  Copy,
  FileText,
  GitDiff,
  LinkSimple,
  Plus,
  ShieldCheck,
  SpinnerGap,
  UploadSimple,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api, dateLabel, when, type Workspace } from "./api";
import { Busy, Empty, ErrorNote, Mark, Modal } from "./ui";
import type { Clarification, ClarificationStatus } from "./coordination-types";
import "./bid-coordinator.css";

const activeStatuses = ["queued", "executing", "verifying"];
const statusNames: Record<ClarificationStatus, string> = {
  draft: "Draft request",
  awaiting_reply: "Waiting for reply",
  answered: "Answer received",
  resolved: "Resolved",
};
const reviewGoal = (category: string) =>
  `Review the latest project change against this existing quote for ${category || "low-voltage switchgear packages"}. Identify affected equipment, quantities, accessories, technical requirements, prices, and delivery terms. Check whether current supplier offers cover the changed scope. Prepare specific clarification requests for missing or conflicting information, and stage evidence-linked quote changes for human review. Do not infer missing commercial terms or make commitments.`;

type Props = {
  w: Workspace;
  configured: boolean;
  issueId: string | null;
  reviewRequest: number;
  onSelectIssue: (id: string | null) => void;
  onUpload: () => void;
  onSources: () => void;
  onQuote: () => void;
  onChanges: () => void;
  onReviewInputs: () => void;
  openSource: (id: string) => void;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
};

export function BidCoordinator(props: Props) {
  const {
    w,
    configured,
    issueId,
    reviewRequest,
    onSelectIssue,
    onUpload,
    onSources,
    onQuote,
    onChanges,
    onReviewInputs,
    openSource,
    refresh,
    notify,
  } = props;
  const qc = useQueryClient();
  const {
    data: issues = [],
    isLoading,
    isFetching,
    error,
  } = useQuery({
    queryKey: ["clarifications", w.project.id],
    queryFn: () =>
      api<Clarification[]>(`/projects/${w.project.id}/clarifications`),
    refetchInterval: 2500,
  });
  const [filter, setFilter] = useState<
    "open" | "awaiting_reply" | "answered" | "resolved" | "all"
  >("open");
  const [creating, setCreating] = useState(false);
  const [investigate, setInvestigate] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const detailRef = useRef<HTMLElement>(null);
  const run = w.runs[0] as any;
  const active = w.runs.find((r) =>
    activeStatuses.includes(String(r.status)),
  ) as any;
  const pending = w.proposals.filter((p) => p.status === "pending");
  const unresolved = issues.filter((i) => i.status !== "resolved");
  const waitingRuns = w.runs.filter((r) => r.status === "waiting_for_input");
  const waitingQuestions = new Set(
    waitingRuns.flatMap((r) =>
      (Array.isArray(r.questions) ? r.questions : [])
        .filter((question): question is string => typeof question === "string")
        .map((question) => question.toLowerCase().replace(/\s+/g, " ").trim()),
    ),
  );
  const waitingIssues = unresolved.filter(
    (issue) =>
      ["draft", "awaiting_reply"].includes(issue.status) &&
      (waitingRuns.some(
        (r) => r.id === issue.run_id || r.id === issue.last_run_id,
      ) ||
        waitingQuestions.has(
          issue.question.toLowerCase().replace(/\s+/g, " ").trim(),
        )),
  );
  const waitingDrafts = waitingIssues.filter(
    (issue) => issue.status === "draft",
  );
  const waitingReplies = waitingIssues.filter(
    (issue) => issue.status === "awaiting_reply",
  );
  const selected = issues.find((i) => i.id === issueId);
  const shown = issues.filter(
    (i) =>
      filter === "all" ||
      (filter === "open" ? i.status !== "resolved" : i.status === filter),
  );
  const refreshAll = async () => {
    await Promise.all([
      refresh(),
      qc.invalidateQueries({ queryKey: ["clarifications", w.project.id] }),
      qc.invalidateQueries({ queryKey: ["work-queue"] }),
    ]);
  };
  const showSavedIssue = async (saved: Clarification) => {
    const queryKey = ["clarifications", w.project.id];
    await qc.cancelQueries({ queryKey });
    qc.setQueryData<Clarification[]>(queryKey, (current = []) => {
      const existing = current.find((item) => item.id === saved.id);
      if (existing && existing.revision > saved.revision) return current;
      return [saved, ...current.filter((item) => item.id !== saved.id)];
    });
    await refreshAll();
    setCreating(false);
    onSelectIssue(saved.id);
  };
  useEffect(() => {
    if (reviewRequest > 0) setInvestigate(true);
  }, [reviewRequest]);
  useEffect(() => {
    if (selected) setFilter("all");
  }, [selected?.id]);
  useEffect(() => {
    if (issueId || creating) detailRef.current?.focus({ preventScroll: true });
  }, [issueId, creating]);

  const perform = async (action: () => Promise<unknown>, message?: string) => {
    setBusy(true);
    setActionError("");
    try {
      await action();
      await refreshAll();
      if (message) notify(message);
      return true;
    } catch (e) {
      setActionError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const start = async () => {
    const ok = await perform(
      () =>
        api(`/quotes/${w.quote.id}/agent-runs`, {
          goal:
            reviewGoal(w.project.category) +
            (instructions.trim()
              ? `\n\nEstimator context: ${instructions.trim()}`
              : ""),
          selected_lines: [],
        }),
      "Rivet is reviewing the changed scope and supplier coverage.",
    );
    if (ok) {
      setInvestigate(false);
      setInstructions("");
    }
  };
  const untracked = [
    ...(run?.questions ?? []),
    ...(run?.result?.clarifications ?? []),
  ]
    .filter((q): q is string => typeof q === "string" && !!q.trim())
    .filter(
      (q, index, arr) =>
        arr.indexOf(q) === index &&
        !issues.some(
          (i) =>
            i.question.toLowerCase().replace(/\s+/g, " ").trim() ===
            q.toLowerCase().replace(/\s+/g, " ").trim(),
        ),
    );
  const next = active
    ? {
        eyebrow: "Rivet is working",
        title: "Checking the change against your quote.",
        text: "Scope, supplier coverage, prices, and delivery are being checked against the current evidence.",
        label: "View source documents",
        action: onSources,
        icon: SpinnerGap,
      }
    : waitingDrafts.length
      ? {
          eyebrow: "Rivet needs a detail to continue",
          title:
            waitingDrafts.length === 1
              ? "One question needs an answer."
              : `${waitingDrafts.length === 2 ? "Two" : waitingDrafts.length} questions need an answer.`,
          text: "The recheck found information that still needs confirming. Review these questions and record the replies so Rivet can continue with the new evidence.",
          label: "Review open question",
          action: () => onSelectIssue(waitingDrafts[0].id),
          icon: ClipboardText,
        }
      : waitingReplies.length
        ? {
            eyebrow: "The recheck is waiting",
            title: "The next step is a reply.",
            text: "The outstanding questions have been requested. Record the replies and their evidence when they arrive so Rivet can continue the recheck.",
            label: "Record a reply",
            action: () => onSelectIssue(waitingReplies[0].id),
            icon: Clock,
          }
        : pending.length
          ? {
              eyebrow: "A decision is ready",
              title: `${pending.length === 1 ? "A proposed revision is" : `${pending.length} proposed revisions are`} ready to review.`,
              text: "Review the affected lines and their evidence. Applying a proposal creates a draft; approval stays with your team.",
              label: "Review proposed changes",
              action: onChanges,
              icon: GitDiff,
            }
          : unresolved.some((i) => i.status === "answered")
            ? {
                eyebrow: "Move the work forward",
                title: "An answer is ready to check.",
                text: "Use the reply and its source to recheck the quote, then review any proposed changes before resolving the issue.",
                label: "Review answer",
                action: () =>
                  onSelectIssue(
                    unresolved.find((i) => i.status === "answered")!.id,
                  ),
                icon: CheckCircle,
              }
            : unresolved.some((i) => i.status === "draft")
              ? {
                  eyebrow: "Your next action",
                  title: "Prepare the missing-information request.",
                  text: "Review the question, copy it to your usual email or supplier channel, then record that it was requested.",
                  label: "Review draft request",
                  action: () =>
                    onSelectIssue(
                      unresolved.find((i) => i.status === "draft")!.id,
                    ),
                  icon: ClipboardText,
                }
              : unresolved.length
                ? {
                    eyebrow: "Waiting on answers",
                    title: "The open issues stay with the quote.",
                    text: "When a supplier responds, add the reply and its source here. Rivet can then pick up the work with the latest evidence.",
                    label: "Record a reply",
                    action: () => onSelectIssue(unresolved[0].id),
                    icon: Clock,
                  }
                : {
                    eyebrow: "Project coordination",
                    title: w.quote.lines.length
                      ? "Keep this bid current as the scope changes."
                      : "Start with the existing quote and project change.",
                    text: w.quote.lines.length
                      ? "Add the revised requirements and supplier offers. Rivet checks what changed, investigates gaps, and prepares the next action."
                      : "Bring in the quote you are updating, then add the revised requirements and supplier offers. This gives Rivet a baseline for identifying what changed.",
                    label:
                      w.quote.lines.length && w.documents.length
                        ? "Review a project change"
                        : !w.quote.lines.length && w.documents.length
                          ? "Set up the existing quote"
                          : "Add source documents",
                    action:
                      w.quote.lines.length && w.documents.length
                        ? () => setInvestigate(true)
                        : !w.quote.lines.length && w.documents.length
                          ? onQuote
                          : onUpload,
                    icon: GitDiff,
                  };
  const NextIcon = next.icon;

  return (
    <section className="bid-coordinator" aria-label="Project work">
      <div className="bid-next-action">
        <div className={"bid-next-mark" + (active ? " is-working" : "")}>
          <NextIcon size={25} />
        </div>
        <div className="bid-next-copy">
          <span className="bid-kicker">{next.eyebrow}</span>
          <h2>{next.title}</h2>
          <p>{next.text}</p>
        </div>
        <button className="primary" onClick={next.action}>
          {next.label}
          <ArrowRight size={16} />
        </button>
      </div>
      <div className="bid-progress" aria-label="Bid coordination workflow">
        {[
          "Review change",
          "Resolve open issues",
          "Review proposal",
          "Approve quote",
        ].map((label, i) => (
          <span
            key={label}
            className={
              i ===
              (active
                ? 0
                : unresolved.length
                  ? 1
                  : pending.length
                    ? 2
                    : w.quote.status === "approved"
                      ? 3
                      : 0)
                ? "is-current"
                : ""
            }
          >
            <b>{String(i + 1).padStart(2, "0")}</b>
            {label}
            {i < 3 && <ArrowRight size={13} />}
          </span>
        ))}
      </div>
      {actionError && <ErrorNote message={actionError} />}
      {run && (
        <details className="bid-run-summary" open={!!active}>
          <summary>
            <span className="bid-run-symbol">
              <Mark small />
            </span>
            <span>
              {active
                ? "Rivet is investigating"
                : run.status === "waiting_for_input"
                  ? "Rivet found a question to resolve"
                  : "Latest investigation"}
              <small>
                {when(String(run.created_at ?? w.project.updated_at))}
              </small>
            </span>
            <span className="bid-run-state">
              {String(run.status).replaceAll("_", " ")}
            </span>
            <ArrowUpRight size={15} />
          </summary>
          <div className="bid-run-body">
            <p>
              {run.result?.error ??
                run.result?.summary ??
                (active
                  ? "Checking the current quote and available documents. Proposed changes will appear for your review."
                  : run.goal?.split("\n")[0])}
            </p>
            {(run.steps?.length ?? 0) > 0 && (
              <ol>
                {run.steps.slice(-4).map((step: any, i: number) => (
                  <li key={i}>
                    <Check size={13} />
                    <span>
                      {step.tool?.replaceAll("_", " ")}
                      {step.result?.summary ? ` — ${step.result.summary}` : ""}
                    </span>
                  </li>
                ))}
              </ol>
            )}
            {active && (
              <button
                className="text-button"
                disabled={busy}
                onClick={() =>
                  void perform(
                    () => api(`/agent-runs/${active.id}/cancel`, {}),
                    "Investigation stopped.",
                  )
                }
              >
                Stop investigation
              </button>
            )}
          </div>
        </details>
      )}
      {untracked.length > 0 && (
        <div className="bid-untracked">
          <h3>Questions from the investigation</h3>
          {untracked.map((question, i) => (
            <div key={question}>
              <p>{question}</p>
              <button
                className="text-button"
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    const issue = await api<Clarification>(
                      `/projects/${w.project.id}/clarifications`,
                      {
                        title: `Clarification ${i + 1}`,
                        question,
                        recipient: "",
                        due_date: null,
                        line_ids: [],
                        source_ids: [],
                        run_id: run.id,
                      },
                    );
                    await showSavedIssue(issue);
                  }, "Question added to open work.")
                }
              >
                <Plus size={14} />
                Track this question
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="bid-work-heading">
        <div>
          <h2>
            Open issues <span>{unresolved.length}</span>
          </h2>
          <p>
            Questions, supplier replies, and the decisions that complete this
            quote.
          </p>
        </div>
        <div>
          <button className="quiet-button" onClick={onUpload}>
            <UploadSimple size={16} />
            Add a source
          </button>
          <button
            className="secondary"
            onClick={() => {
              setCreating(true);
              onSelectIssue(null);
            }}
          >
            <Plus size={16} />
            New request
          </button>
        </div>
      </div>
      <div
        className="bid-filters"
        role="group"
        aria-label="Filter clarification requests"
      >
        {(
          [
            ["open", "Open"],
            ["awaiting_reply", "Waiting"],
            ["answered", "Answered"],
            ["resolved", "Resolved"],
            ["all", "All"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            aria-pressed={filter === value}
            className={filter === value ? "active" : ""}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && <ErrorNote message={(error as Error).message} />}
      {isLoading ? (
        <Busy text="Loading project work…" />
      ) : (
        <div
          className={
            "bid-issue-layout" + (selected || creating ? " has-detail" : "")
          }
        >
          <div className="bid-issue-list">
            {shown.length ? (
              shown.map((issue) => (
                <button
                  key={issue.id}
                  className={
                    "bid-issue-row" +
                    (selected?.id === issue.id ? " is-selected" : "")
                  }
                  onClick={() => {
                    setCreating(false);
                    onSelectIssue(issue.id);
                  }}
                >
                  <IssueStatus status={issue.status} iconOnly />
                  <span className="bid-issue-row-copy">
                    <strong>{issue.title}</strong>
                    <span>
                      {issue.recipient || "Recipient not set"}
                      {issue.due_date && ` · Due ${dateLabel(issue.due_date)}`}
                    </span>
                  </span>
                  <IssueStatus status={issue.status} />
                  <ArrowUpRight size={16} />
                </button>
              ))
            ) : (
              <Empty
                icon={<CheckCircle size={28} />}
                title={
                  filter === "open"
                    ? "No open clarification requests"
                    : "No requests in this view"
                }
              >
                {filter === "open"
                  ? "Review a project change to identify gaps, or add a specific question for your supplier."
                  : "Requests will appear here as the work moves forward."}
              </Empty>
            )}
          </div>
          {(selected || creating) && (
            <aside
              ref={detailRef}
              tabIndex={-1}
              className="bid-issue-detail"
              aria-label={
                creating ? "New clarification request" : "Clarification details"
              }
            >
              <div className="bid-detail-toolbar">
                <span>
                  {creating ? "New clarification" : "Clarification request"}
                </span>
                <button
                  className="icon-button"
                  aria-label="Close clarification details"
                  onClick={() => {
                    setCreating(false);
                    onSelectIssue(null);
                  }}
                >
                  <X size={18} />
                </button>
              </div>
              <IssueDetail
                key={creating ? "new" : selected!.id}
                issue={creating ? undefined : selected}
                w={w}
                configured={configured}
                activeRun={!!active}
                openSource={openSource}
                onQuote={onQuote}
                onReviewInputs={onReviewInputs}
                onChanges={onChanges}
                onUpload={onUpload}
                notify={notify}
                onSaved={showSavedIssue}
                onRefresh={refreshAll}
              />
            </aside>
          )}
        </div>
      )}
      {issueId &&
        !selected &&
        !isLoading &&
        !isFetching &&
        !error &&
        !creating && (
          <p className="bid-missing-issue">
            This clarification could not be found.{" "}
            <button className="text-button" onClick={() => onSelectIssue(null)}>
              View current work
            </button>
          </p>
        )}
      <div className="bid-approval-note">
        <ShieldCheck size={16} />
        <p>
          Rivet prepares the work. Your team approves equipment choices, prices,
          and commercial commitments.
        </p>
        <button className="text-button" onClick={onQuote}>
          Open quote
          <ArrowRight size={14} />
        </button>
      </div>
      {investigate && (
        <Modal
          title="Review a project change"
          eyebrow="Bid coordinator"
          onClose={() => setInvestigate(false)}
        >
          <div className="bid-investigate">
            <p>
              Rivet will compare the current quote with your source documents,
              check supplier coverage, and prepare clarification requests or
              proposed changes.
            </p>
            <label>
              Additional context <span>optional</span>
              <textarea
                rows={4}
                placeholder="For example: Addendum 03 increases the switchgear lineup from eight sections to ten. Check the supplier’s coverage and delivery."
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
              />
            </label>
            {!configured && (
              <ErrorNote message="The AI connection needs server configuration. You can still track requests, record replies, and edit the quote." />
            )}
            {active && (
              <p>
                An investigation is already running. Wait for it to finish or
                stop it before starting another.
              </p>
            )}
            {actionError && <ErrorNote message={actionError} />}
            <div className="bid-form-actions">
              <button
                className="secondary"
                onClick={() => {
                  setInvestigate(false);
                  onUpload();
                }}
              >
                <UploadSimple size={15} />
                Add source first
              </button>
              <button
                className="primary"
                disabled={
                  busy || !configured || !!active || !w.documents.length
                }
                onClick={() => void start()}
              >
                {busy ? (
                  <SpinnerGap className="spin" size={16} />
                ) : (
                  <ArrowRight size={16} />
                )}
                Start review
              </button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}

function IssueStatus({
  status,
  iconOnly = false,
}: {
  status: ClarificationStatus;
  iconOnly?: boolean;
}) {
  const Icon =
    status === "resolved"
      ? CheckCircle
      : status === "answered"
        ? ClipboardText
        : status === "awaiting_reply"
          ? Clock
          : FileText;
  return (
    <span
      className={`bid-issue-status ${status}${iconOnly ? " icon-only" : ""}`}
      aria-label={iconOnly ? statusNames[status] : undefined}
    >
      <Icon size={iconOnly ? 19 : 13} />
      {!iconOnly && statusNames[status]}
    </span>
  );
}

function IssueDetail({
  issue,
  w,
  configured,
  activeRun,
  openSource,
  onQuote,
  onReviewInputs,
  onChanges,
  onUpload,
  notify,
  onSaved,
  onRefresh,
}: {
  issue?: Clarification;
  w: Workspace;
  configured: boolean;
  activeRun: boolean;
  openSource: (id: string) => void;
  onQuote: () => void;
  onReviewInputs: () => void;
  onChanges: () => void;
  onUpload: () => void;
  notify: (message: string) => void;
  onSaved: (issue: Clarification) => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(!issue);
  const [editRevision, setEditRevision] = useState(issue?.revision);
  const [answerRevision, setAnswerRevision] = useState(issue?.revision);
  const [resolutionRevision, setResolutionRevision] = useState(issue?.revision);
  const [title, setTitle] = useState(issue?.title ?? "");
  const [question, setQuestion] = useState(issue?.question ?? "");
  const [recipient, setRecipient] = useState(issue?.recipient ?? "");
  const [due, setDue] = useState(issue?.due_date ?? "");
  const [lineIds, setLineIds] = useState<string[]>(issue?.line_ids ?? []);
  const [sourceIds, setSourceIds] = useState<string[]>(issue?.source_ids ?? []);
  const [answer, setAnswer] = useState("");
  const [answerSources, setAnswerSources] = useState<string[]>([]);
  const [resolution, setResolution] = useState("");
  const [recording, setRecording] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const revision = issue?.revision;
  const run = issue?.last_run_id
    ? (w.runs.find((r) => r.id === issue.last_run_id) as any)
    : null;
  const needsReconcile = w.quote.reconciled_input !== w.quote.input_revision;
  const processingSources = w.documents.some(
    (doc) => !["ready", "mapped"].includes(doc.state),
  );
  const recheckUnfinished =
    run && [...activeStatuses, "waiting_for_input"].includes(run.status);
  const recheckProposal =
    run?.result?.proposal_id &&
    w.proposals.some(
      (proposal) =>
        proposal.id === run.result.proposal_id && proposal.status === "pending",
    );
  const linkedUnreviewed = w.quote.lines.some(
    (line) => issue?.line_ids.includes(line.id) && line.review !== "approved",
  );
  const update = async (
    action: () => Promise<Clarification>,
    message: string,
  ) => {
    setBusy(true);
    setError("");
    try {
      const saved = await action();
      await onSaved(saved);
      notify(message);
      return true;
    } catch (e) {
      setError((e as Error).message);
      await onRefresh().catch(() => {});
      return false;
    } finally {
      setBusy(false);
    }
  };
  const patch = (
    body: Record<string, unknown>,
    message: string,
    expectedRevision = revision,
  ) =>
    update(
      () =>
        api<Clarification>(
          `/clarifications/${issue!.id}`,
          { expected_revision: expectedRevision, ...body },
          "PATCH",
        ),
      message,
    );
  const save = async () => {
    const body = {
      title: title.trim(),
      question: question.trim(),
      recipient: recipient.trim(),
      due_date: due || null,
    };
    const ok = await update(
      () =>
        issue
          ? api<Clarification>(
              `/clarifications/${issue.id}`,
              {
                expected_revision: editRevision,
                title: body.title,
                ...(issue.answer ? {} : { question: body.question }),
                recipient: body.recipient,
                due_date: body.due_date,
              },
              "PATCH",
            )
          : api<Clarification>(`/projects/${w.project.id}/clarifications`, {
              ...body,
              line_ids: lineIds,
              source_ids: sourceIds,
              run_id: null,
            }),
      issue ? "Request updated." : "Clarification draft saved.",
    );
    if (ok) setEditing(false);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(
        `${issue!.title}\n\n${issue!.question}`,
      );
      notify("Request copied. Send it through your usual supplier channel.");
    } catch {
      setError(
        "Clipboard access is unavailable. Select and copy the question text.",
      );
    }
  };
  return (
    <div className="bid-detail-content">
      {issue && (
        <div className="bid-detail-status">
          <IssueStatus status={issue.status} />
          <span>Updated {when(issue.updated_at)}</span>
        </div>
      )}
      {error && <ErrorNote message={error} />}
      {editing ? (
        <form
          className="bid-request-form"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <label>
            Request title
            <input
              required
              autoFocus
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Updated offer for the revised switchgear scope"
            />
          </label>
          <label>
            Question for the recipient
            <textarea
              required
              rows={5}
              readOnly={!!issue?.answer}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Which quantities, accessories, prices, or delivery terms need confirmation?"
            />
            {issue?.answer && (
              <small>The question is kept with its recorded answer.</small>
            )}
          </label>
          <div className="bid-form-columns">
            <label>
              Recipient <span>optional</span>
              <input
                value={recipient}
                onChange={(e) => setRecipient(e.target.value)}
                placeholder="Supplier or contact"
              />
            </label>
            <label>
              Reply due <span>optional</span>
              <input
                type="date"
                value={due}
                onChange={(e) => setDue(e.target.value)}
              />
            </label>
          </div>
          {!issue && (
            <>
              <details className="bid-evidence-picker">
                <summary>
                  Link affected quote lines{" "}
                  <span>{lineIds.length} selected</span>
                </summary>
                <div>
                  {w.quote.lines.length ? (
                    w.quote.lines.map((line) => (
                      <label key={line.id}>
                        <input
                          type="checkbox"
                          checked={lineIds.includes(line.id)}
                          onChange={() => setLineIds(toggle(lineIds, line.id))}
                        />
                        <span>
                          <strong>{line.tag}</strong>
                          {line.description}
                        </span>
                      </label>
                    ))
                  ) : (
                    <p>Add quote lines to link affected equipment.</p>
                  )}
                </div>
              </details>
              <SourcePicker
                w={w}
                selected={sourceIds}
                onChange={setSourceIds}
                openSource={openSource}
                label="Link supporting evidence"
              />
            </>
          )}
          <div className="bid-form-actions">
            {issue && (
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => {
                  setEditing(false);
                  setTitle(issue.title);
                  setQuestion(issue.question);
                  setRecipient(issue.recipient);
                  setDue(issue.due_date ?? "");
                }}
              >
                Cancel
              </button>
            )}
            <button
              className="primary"
              disabled={busy || !title.trim() || !question.trim()}
            >
              {busy ? "Saving…" : issue ? "Save changes" : "Save request"}
              <Check size={15} />
            </button>
          </div>
        </form>
      ) : (
        issue && (
          <>
            <h3 className="bid-request-title">{issue.title}</h3>
            <p className="bid-request-question">{issue.question}</p>
            <dl className="bid-request-meta">
              <div>
                <dt>Recipient</dt>
                <dd>{issue.recipient || "Not set"}</dd>
              </div>
              <div>
                <dt>Reply due</dt>
                <dd>
                  {issue.due_date ? dateLabel(issue.due_date) : "No date set"}
                </dd>
              </div>
              {issue.line_ids.length > 0 && (
                <div>
                  <dt>Affected equipment</dt>
                  <dd>
                    {issue.line_ids
                      .map(
                        (id) =>
                          w.quote.lines.find((line) => line.id === id)?.tag ??
                          "Removed line",
                      )
                      .join(", ")}
                  </dd>
                </div>
              )}
            </dl>
            <SourceLinks ids={issue.source_ids} w={w} openSource={openSource} />
            <div className="bid-request-actions">
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void copy()}
              >
                <Copy size={15} />
                Copy request
              </button>
              {issue.status !== "resolved" && (
                <button
                  className="text-button"
                  onClick={() => {
                    setTitle(issue.title);
                    setQuestion(issue.question);
                    setRecipient(issue.recipient);
                    setDue(issue.due_date ?? "");
                    setEditRevision(issue.revision);
                    setEditing(true);
                  }}
                >
                  Edit details
                </button>
              )}
            </div>
            {(issue.status === "draft" ||
              issue.status === "awaiting_reply") && (
              <div className="bid-next-step">
                <span className="bid-kicker">
                  {issue.status === "draft"
                    ? "Request the missing information"
                    : "Waiting for the recipient"}
                </span>
                <p>
                  {issue.status === "draft"
                    ? "Copy this request into your email or supplier channel. Once requested, mark it here so the team can follow up."
                    : "This tracks the request you made outside Rivet. Record the reply here when it arrives."}
                </p>
                <div className="bid-form-actions">
                  {issue.status === "draft" ? (
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={() =>
                        void patch(
                          { status: "awaiting_reply" },
                          "Marked as requested. No message was sent by Rivet.",
                        )
                      }
                    >
                      <Clock size={15} />
                      Mark as requested
                    </button>
                  ) : (
                    <button
                      className="text-button"
                      disabled={busy}
                      onClick={() =>
                        void patch(
                          { status: "draft" },
                          "Request returned to draft.",
                        )
                      }
                    >
                      Return to draft
                    </button>
                  )}
                  <button
                    className={
                      issue.status === "awaiting_reply"
                        ? "primary"
                        : "secondary"
                    }
                    disabled={busy}
                    onClick={() => {
                      setAnswerRevision(issue.revision);
                      setRecording(!recording);
                    }}
                  >
                    <ClipboardText size={15} />
                    Record answer
                  </button>
                </div>
              </div>
            )}
            {recording && (
              <form
                className="bid-answer-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void (async () => {
                    const ok = await update(
                      () =>
                        api<Clarification>(
                          `/clarifications/${issue.id}/answers`,
                          {
                            expected_revision: answerRevision,
                            answer: answer.trim(),
                            source_ids: answerSources,
                          },
                        ),
                      "Answer recorded. Recheck it against the quote before resolving.",
                    );
                    if (ok) {
                      setRecording(false);
                      setAnswer("");
                      setAnswerSources([]);
                    }
                  })();
                }}
              >
                <h4>Record the recipient’s answer</h4>
                <label>
                  Answer
                  <textarea
                    required
                    rows={5}
                    value={answer}
                    onChange={(e) => setAnswer(e.target.value)}
                    placeholder="Paste the reply, including any qualifications or conditions."
                  />
                </label>
                <SourcePicker
                  w={w}
                  selected={answerSources}
                  onChange={setAnswerSources}
                  openSource={openSource}
                  label="Link reply evidence"
                />
                <p>
                  Replies are recorded as human-entered information. Attach an
                  updated offer or email as a source when available.
                </p>
                <button
                  type="button"
                  className="text-button"
                  onClick={onUpload}
                >
                  <UploadSimple size={14} />
                  Add the reply as a source
                </button>
                <div className="bid-form-actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setRecording(false)}
                  >
                    Cancel
                  </button>
                  <button className="primary" disabled={busy || !answer.trim()}>
                    Save answer
                    <Check size={15} />
                  </button>
                </div>
              </form>
            )}
            {issue.answer && (
              <section className="bid-recorded-answer">
                <div>
                  <h4>Recorded answer</h4>
                  <span>{issue.answer_origin || "Human entered"}</span>
                </div>
                <p>{issue.answer}</p>
                <SourceLinks
                  ids={issue.answer_source_ids}
                  w={w}
                  openSource={openSource}
                />
                {issue.status === "answered" && (
                  <button
                    className="text-button"
                    onClick={() => {
                      setAnswer(issue.answer);
                      setAnswerSources(issue.answer_source_ids);
                      setAnswerRevision(issue.revision);
                      setRecording(true);
                    }}
                  >
                    Correct or supplement answer
                  </button>
                )}
              </section>
            )}
            {issue.status === "answered" && (
              <div className="bid-next-step">
                <span className="bid-kicker">
                  Check the answer against the quote
                </span>
                <p>
                  Rivet reviews the answer with the latest sources and pricing
                  rules. Any proposed change still needs your approval.
                </p>
                {run && (
                  <p className="bid-recheck-state">
                    Latest recheck: {String(run.status).replaceAll("_", " ")}
                    {run.result?.error ? ` — ${run.result.error}` : ""}
                  </p>
                )}
                <div className="bid-form-actions">
                  <button
                    className="primary"
                    disabled={busy || !configured || activeRun}
                    onClick={() =>
                      void update(async () => {
                        const result = await api<{
                          clarification: Clarification;
                          run_id: string;
                        }>(`/clarifications/${issue.id}/resume`, {
                          expected_revision: revision,
                        });
                        return result.clarification;
                      }, "Rivet is rechecking the answer against the current quote.")
                    }
                  >
                    <ArrowClockwise size={15} />
                    {activeRun ? "Investigation running" : "Recheck with Rivet"}
                  </button>
                  <button className="text-button" onClick={onChanges}>
                    Review proposals
                    <ArrowRight size={14} />
                  </button>
                </div>
                {!configured && (
                  <p className="bid-help">
                    AI rechecking needs the server connection. You can review
                    the evidence and quote manually.
                  </p>
                )}
                <button
                  className="bid-resolve-toggle text-button"
                  onClick={() => {
                    setResolutionRevision(issue.revision);
                    setResolving(!resolving);
                  }}
                >
                  <CheckCircle size={15} />
                  Resolve after review
                </button>
              </div>
            )}
            {resolving && issue.status === "answered" && (
              <form
                className="bid-resolution-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void (async () => {
                    const ok = await patch(
                      {
                        status: "resolved",
                        resolution_note: resolution.trim(),
                      },
                      "Clarification resolved. Its evidence stays with the quote.",
                      resolutionRevision,
                    );
                    if (ok) setResolving(false);
                  })();
                }}
              >
                <h4>Confirm the issue is resolved</h4>
                {processingSources && (
                  <p className="bid-help">
                    Finish source processing before resolving this issue.
                  </p>
                )}
                {recheckUnfinished && (
                  <div className="bid-resolution-check">
                    <Clock size={16} />
                    <p>
                      Finish or stop the recheck before resolving this issue.
                    </p>
                    <button
                      type="button"
                      className="text-button"
                      disabled={busy}
                      onClick={() =>
                        void update(async () => {
                          await api(`/agent-runs/${run.id}/cancel`, {});
                          return issue;
                        }, "Recheck stopped.")
                      }
                    >
                      Stop recheck
                    </button>
                  </div>
                )}
                {recheckProposal && (
                  <div className="bid-resolution-check">
                    <GitDiff size={16} />
                    <p>The recheck has a proposed quote change to review.</p>
                    <button
                      type="button"
                      className="text-button"
                      onClick={onChanges}
                    >
                      Review proposal
                      <ArrowRight size={13} />
                    </button>
                  </div>
                )}
                {needsReconcile && (
                  <div className="bid-resolution-check">
                    <WarningCircle size={16} />
                    <p>Current source documents need to be reconciled.</p>
                    <button
                      type="button"
                      className="text-button"
                      onClick={onReviewInputs}
                    >
                      Review sources
                      <ArrowRight size={13} />
                    </button>
                  </div>
                )}
                {linkedUnreviewed && (
                  <div className="bid-resolution-check">
                    <WarningCircle size={16} />
                    <p>Affected equipment lines still need your review.</p>
                    <button
                      type="button"
                      className="text-button"
                      onClick={onQuote}
                    >
                      Review quote
                      <ArrowRight size={13} />
                    </button>
                  </div>
                )}
                <label>
                  Resolution note
                  <textarea
                    required
                    rows={3}
                    value={resolution}
                    onChange={(e) => setResolution(e.target.value)}
                    placeholder="Explain how the reply and reviewed quote resolve the original question."
                  />
                </label>
                <button
                  className="primary"
                  disabled={
                    busy ||
                    !resolution.trim() ||
                    needsReconcile ||
                    linkedUnreviewed ||
                    processingSources ||
                    !!recheckUnfinished ||
                    !!recheckProposal
                  }
                >
                  <CheckCircle size={15} />
                  Mark resolved
                </button>
              </form>
            )}
            {issue.status === "resolved" && (
              <section className="bid-resolution-complete">
                <CheckCircle size={20} />
                <div>
                  <h4>Resolved with a recorded decision</h4>
                  <p>{issue.resolution_note}</p>
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() =>
                      void patch(
                        { status: "draft" },
                        "Request reopened. Record a fresh answer before resolving it again.",
                      )
                    }
                  >
                    Reopen request
                  </button>
                </div>
              </section>
            )}
          </>
        )
      )}
    </div>
  );
}

function toggle(values: string[], value: string) {
  return values.includes(value)
    ? values.filter((v) => v !== value)
    : [...values, value];
}

function sourceLabel(source: Record<string, unknown>, w: Workspace) {
  const doc = w.documents.find((d) => d.id === source.document_id);
  const position = (source.location ?? {}) as Record<string, unknown>;
  const location = position.page
    ? ` · p. ${position.page}`
    : position.sheet
      ? ` · ${position.sheet}${position.row ? `, row ${position.row}` : position.cells ? `, ${position.cells}` : ""}`
      : position.line
        ? ` · Line ${position.line}`
        : position.cells
          ? ` · Cells ${position.cells}`
          : "";
  return (
    (doc?.name ?? String(source.document_name ?? "Project source")) + location
  );
}

function SourceLinks({
  ids,
  w,
  openSource,
}: {
  ids: string[];
  w: Workspace;
  openSource: (id: string) => void;
}) {
  if (!ids.length) return null;
  return (
    <div className="bid-source-links">
      {ids.map((id) => {
        const source = w.sources.find((s) => s.id === id);
        return source ? (
          <button key={id} onClick={() => openSource(id)}>
            <LinkSimple size={13} />
            <span>{sourceLabel(source, w)}</span>
            <ArrowUpRight size={12} />
          </button>
        ) : (
          <span key={id}>Source no longer available</span>
        );
      })}
    </div>
  );
}

function SourcePicker({
  w,
  selected,
  onChange,
  openSource,
  label,
}: {
  w: Workspace;
  selected: string[];
  onChange: (ids: string[]) => void;
  openSource: (id: string) => void;
  label: string;
}) {
  const [query, setQuery] = useState("");
  const matched = w.sources.filter((source) =>
    [sourceLabel(source, w), source.text]
      .join(" ")
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <details className="bid-evidence-picker">
      <summary>
        {label}
        <span>{selected.length} selected</span>
      </summary>
      <div>
        {w.sources.length ? (
          <>
            <input
              aria-label={`${label}: filter sources`}
              placeholder="Find a document or source detail…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {matched.slice(0, 40).map((source) => {
              const id = String(source.id);
              return (
                <div className="bid-source-option" key={id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={selected.includes(id)}
                      onChange={() => onChange(toggle(selected, id))}
                    />
                    <span>
                      <strong>{sourceLabel(source, w)}</strong>
                      <small>
                        {String(
                          source.text ?? source.excerpt ?? "Source evidence",
                        ).slice(0, 160)}
                      </small>
                    </span>
                  </label>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`View ${sourceLabel(source, w)}`}
                    onClick={() => openSource(id)}
                  >
                    <ArrowUpRight size={14} />
                  </button>
                </div>
              );
            })}
            {matched.length > 40 && (
              <p>
                Showing the first 40 matches. Filter to find a specific source.
              </p>
            )}
            {matched.length === 0 && <p>No sources match this search.</p>}
          </>
        ) : (
          <p>Add and process a source document to link its evidence.</p>
        )}
      </div>
    </details>
  );
}
