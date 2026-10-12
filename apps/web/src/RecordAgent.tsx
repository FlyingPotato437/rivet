import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import {
  ArrowRight,
  ArrowUpRight,
  ArrowCounterClockwise,
  Check,
  CircleNotch,
  Clock,
  X,
  WarningCircle,
  CaretDown,
  Plus,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { useAccount } from "./Auth";
import { Busy, ErrorNote, RivetIcon } from "./ui";
import { ActivityAttribution, ActorName } from "./RecordAttribution";
import { agentRequestText, agentTaskTitle } from "./record-agent-intents";
import type { RecordView } from "./record-types";
import type {
  RecordAgentAnswer,
  RecordAgentProposal,
  RecordAgentRun,
  RecordAgentView,
  RecordConversation,
} from "./record-agent-types";
const runLabels: Record<RecordAgentRun["status"], string> = {
  queued: "Queued",
  running: "Working",
  waiting_for_review: "Ready for review",
  waiting_for_input: "Needs your input",
  completed: "Completed",
  failed: "Needs attention",
  cancelled: "Stopped",
};
const activeStatus = (status: string) =>
  status === "queued" || status === "running";
export type AgentWorkState = {
  ready: number;
  needsInput: number;
  working: boolean;
};
export type AgentTaskRequest = { id: string; goal: string };

export function RecordAgent({
  w,
  open,
  setOpen,
  save,
  saveError,
  recordBusy,
  focusedCommentId,
  focusedSourceId,
  onWorkStateChange,
  taskRequest,
  composerRequest,
  onTaskRequestConsumed,
  openSource,
  openComment,
  openChanges,
  openWork,
}: {
  w: RecordView;
  open: boolean;
  setOpen: (open: boolean) => void;
  save: (path: string, body: unknown) => Promise<boolean>;
  saveError: string;
  recordBusy: boolean;
  focusedCommentId: string;
  focusedSourceId: string;
  onWorkStateChange?: (state: AgentWorkState) => void;
  taskRequest?: AgentTaskRequest | null;
  composerRequest?: number;
  onTaskRequestConsumed?: (id: string) => void;
  openSource: (id: string) => void;
  openComment: (id: string) => void;
  openChanges: (changeId?: string) => void;
  openWork: () => void;
}) {
  const account = useAccount();
  const qc = useQueryClient();
  const base = `/orders/${w.order.id}/record`;
  const linkedRun = new URLSearchParams(location.hash.split("?")[1] ?? "").get(
    "run",
  );
  const linkedProposal = new URLSearchParams(
    location.hash.split("?")[1] ?? "",
  ).get("proposal");
  const agent = useQuery({
    queryKey: ["record-agent", w.order.id],
    queryFn: () => api<RecordAgentView>(`${base}/agent`),
    refetchInterval: open ? 2500 : 10000,
  });
  const conversation = useQuery({
    queryKey: ["record-conversation", w.order.id],
    queryFn: () => api<RecordConversation>(`${base}/conversation`),
    enabled: open,
    refetchOnWindowFocus: true,
  });
  const [view, setView] = useState<"runs" | "questions" | "activity">("runs");
  const [prompt, setPrompt] = useState("");
  const [composerExpanded, setComposerExpanded] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [proposalId, setProposalId] = useState("");
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [activeQuestion, setActiveQuestion] = useState("");
  const [error, setError] = useState("");
  const [answer, setAnswer] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const lastQuestion = useRef<HTMLDivElement>(null);
  const handledProposalLink = useRef("");
  const autoOpenedProposal = useRef("");
  const handledTaskRequest = useRef("");
  const startingRun = useRef(false);
  const settingsEditVersion = useRef<number | null>(null);
  const scopeComment = w.comments.find(
    (comment) => comment.id === focusedCommentId,
  );
  const scopeSource = w.sources.find((source) => source.id === focusedSourceId);
  const scopeDocument = w.documents.find(
    (document) => document.id === (scopeSource?.document_id || focusedSourceId),
  );
  const recentRuns = agent.data?.runs ?? [];
  const requestedRunId = selectedRunId || linkedRun || "";
  const olderRun = useQuery({
    queryKey: ["record-agent-run", w.order.id, requestedRunId],
    queryFn: () => api<RecordAgentRun>(`${base}/agent/runs/${requestedRunId}`),
    enabled:
      open &&
      !!requestedRunId &&
      agent.isSuccess &&
      !recentRuns.some((run) => run.id === requestedRunId),
    retry: false,
    refetchInterval: (query) =>
      activeStatus(query.state.data?.status || "") ? 2500 : false,
  });
  const runs =
    olderRun.data && !recentRuns.some((run) => run.id === olderRun.data.id)
      ? [olderRun.data, ...recentRuns]
      : recentRuns;
  const allProposals = [
    ...new Map(
      [
        ...(olderRun.data?.proposals ?? []),
        ...(agent.data?.proposals ?? []),
      ].map((proposal) => [proposal.id, proposal]),
    ).values(),
  ];
  const changedEvidence = (runId: string) => {
    const related = allProposals.filter(
      (proposal) => proposal.run_id === runId,
    );
    return (
      related.some(
        (proposal) =>
          proposal.status === "stale" ||
          (proposal.status === "pending" &&
            proposal.base_version !== w.version),
      ) &&
      !related.some(
        (proposal) =>
          proposal.status === "pending" && proposal.base_version === w.version,
      )
    );
  };
  const selectedRun = requestedRunId
    ? runs.find((run) => run.id === requestedRunId)
    : runs[0];
  const evidenceChanged = !!selectedRun && changedEvidence(selectedRun.id);
  const selectedGoal = selectedRun ? agentRequestText(selectedRun.goal) : "";
  const proposals = allProposals.filter(
    (proposal) => proposal.run_id === selectedRun?.id,
  );
  const resultReady =
    !!selectedRun &&
    !activeStatus(selectedRun.status) &&
    selectedRun.status !== "waiting_for_input" &&
    (proposals.length > 0 || !!selectedRun.result?.summary);
  const showingResult = view === "runs" && resultReady;
  const pending = allProposals.filter(
    (proposal) => proposal.status === "pending",
  );
  const active = runs.find((run) => activeStatus(run.status));
  const readyCount = pending.filter(
    (proposal) =>
      proposal.base_version === w.version &&
      !activeStatus(
        runs.find((run) => run.id === proposal.run_id)?.status || "",
      ),
  ).length;
  const inputCount = runs.filter(
    (run) => run.status === "waiting_for_input",
  ).length;
  const defaultProposal =
    proposals.find((proposal) => proposal.status === "pending") ?? proposals[0];
  useEffect(() => {
    onWorkStateChange?.({
      ready: readyCount,
      needsInput: inputCount,
      working: !!active || (busy && startingRun.current),
    });
  }, [readyCount, inputCount, !!active, busy, onWorkStateChange]);
  useEffect(() => {
    if (!selectedRun || activeStatus(selectedRun.status) || !defaultProposal)
      return;
    const key = `${selectedRun.id}:${defaultProposal.id}`;
    if (autoOpenedProposal.current === key) return;
    autoOpenedProposal.current = key;
    setProposalId(defaultProposal.id);
    body.current?.scrollTo({ top: 0 });
  }, [selectedRun?.id, selectedRun?.status, defaultProposal?.id]);
  const actor = account.name || "Workspace user";
  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["record-agent", w.order.id] }),
      qc.invalidateQueries({ queryKey: ["record-agent-run", w.order.id] }),
      qc.invalidateQueries({ queryKey: ["record", w.order.id] }),
      qc.invalidateQueries({ queryKey: ["records"] }),
      qc.invalidateQueries({ queryKey: ["record-inbox"] }),
    ]);
  };
  useEffect(() => {
    if (linkedRun) {
      setSelectedRunId(linkedRun);
      setView("runs");
    }
  }, [linkedRun]);
  useEffect(() => {
    if (!linkedProposal || handledProposalLink.current === linkedProposal)
      return;
    const proposal = allProposals.find((item) => item.id === linkedProposal);
    if (proposal) {
      handledProposalLink.current = linkedProposal;
      setSelectedRunId(proposal.run_id);
      setProposalId(proposal.id);
      setView("runs");
    }
  }, [linkedProposal, agent.data?.proposals, olderRun.data]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === "j" &&
        !document.querySelector(".modal-backdrop")
      ) {
        event.preventDefault();
        setOpen(true);
        setComposerExpanded(true);
        requestAnimationFrame(() => input.current?.focus());
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [setOpen]);
  useEffect(() => {
    if (activeQuestion)
      lastQuestion.current?.scrollIntoView({ block: "nearest" });
  }, [activeQuestion]);
  useEffect(() => {
    if (!composerRequest) return;
    setComposerExpanded(true);
    setView((current) => (current === "activity" ? "runs" : current));
    requestAnimationFrame(() => input.current?.focus());
  }, [composerRequest]);
  const scoped = (goal: string) =>
    [
      scopeComment
        ? `Context: comment ${scopeComment.number} (comment id ${scopeComment.id}).`
        : "",
      scopeDocument
        ? `Source in view: ${scopeDocument.name} (document id ${scopeDocument.id}${scopeSource ? `, source id ${scopeSource.id}, page ${scopeSource.location.page ?? "not stated"}` : ""}).`
        : "",
      goal,
    ]
      .filter(Boolean)
      .join("\n");
  const mutate = async (
    operation: () => Promise<unknown>,
    success?: () => void,
  ) => {
    setBusy(true);
    setError("");
    try {
      await operation();
      await refresh();
      success?.();
      return true;
    } catch (err) {
      setError((err as Error).message);
      await refresh().catch(() => {});
      return false;
    } finally {
      setBusy(false);
    }
  };
  const start = (goal: string, requestId?: string) => {
    if (!goal.trim() || busy || active || startingRun.current) return;
    startingRun.current = true;
    void mutate(async () => {
      const run = await api<RecordAgentRun>(
        `${base}/agent/runs`,
        {
          goal: scoped(goal.trim()),
          expected_version: w.version,
          actor,
          reason: "Requested source-grounded review preparation",
        },
        undefined,
        requestId ? { idempotencyKey: requestId } : undefined,
      );
      qc.setQueryData<RecordAgentView>(
        ["record-agent", w.order.id],
        (current) =>
          current
            ? {
                ...current,
                runs: [
                  run,
                  ...current.runs.filter((item) => item.id !== run.id),
                ],
              }
            : current,
      );
      setSelectedRunId(run.id);
      setProposalId("");
      setView("runs");
      setPrompt("");
      setComposerExpanded(false);
      body.current?.scrollTo({ top: 0 });
    }).finally(() => {
      startingRun.current = false;
    });
  };
  useEffect(() => {
    if (
      !taskRequest ||
      taskRequest.id === handledTaskRequest.current ||
      !agent.isSuccess
    )
      return;
    handledTaskRequest.current = taskRequest.id;
    onTaskRequestConsumed?.(taskRequest.id);
    setView("runs");
    setComposerExpanded(true);
    setPrompt(taskRequest.goal);
    if (active || busy) {
      setPrompt(taskRequest.goal);
      setError(
        "A task is already in progress. Your request is ready below; start it when the current task finishes.",
      );
      return;
    }
    start(taskRequest.goal, taskRequest.id);
  }, [taskRequest?.id, agent.isSuccess]);
  const ask = async (question: string) => {
    if (!question.trim() || asking) return;
    setAsking(true);
    setError("");
    setActiveQuestion(question);
    setPrompt("");
    setView("questions");
    try {
      await api<RecordAgentAnswer>(`${base}/ask`, {
        question: scoped(question.trim()),
        history: (conversation.data?.messages ?? [])
          .slice(-3)
          .flatMap((message) => [
            { role: "user", content: message.question.slice(0, 6000) },
            {
              role: "assistant",
              content: message.result.answer.slice(0, 6000),
            },
          ]),
      });
      await conversation.refetch();
      setActiveQuestion("");
    } catch (err) {
      setError((err as Error).message);
      setPrompt(question);
      setActiveQuestion("");
    } finally {
      setAsking(false);
    }
  };
  const selectedProposal = proposals.find(
    (proposal) => proposal.id === proposalId,
  );
  return (
    <section
      id="record-agent-panel"
      className={
        "record-agent workbench-agent " +
        (open ? "is-open" : "is-closed") +
        (showingResult ? " is-showing-result" : "")
      }
      aria-label="Rivet activity"
      hidden={!open}
    >
      {!showingResult && (
        <div className="record-agent-scope">
          <RivetIcon name="evidence" size={14} />
          <span>
            {scopeComment
              ? `Comment ${scopeComment.number}`
              : scopeDocument?.name || "This order"}
          </span>
          <small>Record v{w.version}</small>
        </div>
      )}
      <nav className="record-agent-tabs" aria-label="Rivet views">
        <button aria-pressed={view === "runs"} onClick={() => setView("runs")}>
          Tasks{pending.length > 0 && <span>{pending.length}</span>}
        </button>
        <button
          aria-pressed={view === "questions"}
          onClick={() => setView("questions")}
        >
          Questions
        </button>
        <button
          aria-pressed={view === "activity"}
          onClick={() => setView("activity")}
        >
          Activity
        </button>
      </nav>
      <div className="record-agent-body" ref={body}>
        {(error || saveError) && <ErrorNote message={error || saveError} />}
        {view === "runs" && (
          <>
            {agent.isError && (
              <div className="record-agent-load-error">
                <ErrorNote message={(agent.error as Error).message} />
                <button
                  className="text-button"
                  onClick={() => void agent.refetch()}
                >
                  Try again
                  <ArrowCounterClockwise size={14} />
                </button>
              </div>
            )}
            {olderRun.isError && (
              <ErrorNote
                message={`This saved task could not be opened. ${(olderRun.error as Error).message}`}
              />
            )}
            {agent.isLoading ||
            (!!requestedRunId && !selectedRun && olderRun.isFetching) ? (
              <Busy text="Loading saved tasks…" />
            ) : (
              <>
                {runs.length > 0 && (
                  <label className="agent-run-picker">
                    <span className={showingResult ? "sr-only" : undefined}>
                      Saved task
                    </span>
                    <select
                      aria-label="Saved Rivet task"
                      value={selectedRun?.id || ""}
                      onChange={(event) => {
                        setSelectedRunId(event.target.value);
                        setProposalId("");
                        setAnswer("");
                        setComposerExpanded(false);
                        body.current?.scrollTo({ top: 0 });
                      }}
                    >
                      {!selectedRun && (
                        <option value="">Choose a saved task</option>
                      )}
                      {runs.map((run) => (
                        <option key={run.id} value={run.id}>
                          {agentTaskTitle(run.goal).slice(0, 70) ||
                            "Review task"}{" "}
                          ·{" "}
                          {changedEvidence(run.id)
                            ? "Evidence changed"
                            : runLabels[run.status]}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {selectedRun ? (
                  <article className="agent-saved-run">
                    {!showingResult && (
                      <>
                        <div className="agent-run-heading">
                          <RivetIcon name="agent" size={20} />
                          <h2>{agentTaskTitle(selectedRun.goal)}</h2>
                        </div>
                        <details
                          className="agent-run-request"
                          key={selectedRun.id}
                        >
                          <summary>Full request</summary>
                          <p>{selectedGoal}</p>
                        </details>
                        <div className="agent-run-meta">
                          <Origin
                            mode={selectedRun.mode}
                            label={
                              selectedRun.mode === "ai"
                                ? "AI task"
                                : "Rule-based task"
                            }
                          />
                          <span
                            className={"agent-run-status " + selectedRun.status}
                          >
                            {activeStatus(selectedRun.status) && (
                              <CircleNotch
                                size={13}
                                className="agent-spinning"
                              />
                            )}
                            {evidenceChanged
                              ? "Evidence changed"
                              : runLabels[selectedRun.status]}
                          </span>
                        </div>
                        <p className="agent-run-time">
                          {when(selectedRun.created_at)} ·{" "}
                          {selectedRun.origin === "event"
                            ? "Triggered by new evidence"
                            : "Requested by your team"}
                        </p>
                      </>
                    )}
                    {selectedRun.result?.summary && !proposals.length && (
                      <p className="agent-run-summary">
                        {selectedRun.result.summary}
                      </p>
                    )}
                    {selectedRun.result?.error && (
                      <ErrorNote message={selectedRun.result.error} />
                    )}

                    {selectedRun.status === "waiting_for_input" && (
                      <form
                        className="agent-input-request"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void mutate(
                            () =>
                              api(
                                `${base}/agent/runs/${selectedRun.id}/resume`,
                                {
                                  expected_version: w.version,
                                  actor,
                                  reason:
                                    "Recorded human clarification for this task",
                                  answer: answer.trim(),
                                },
                              ),
                            () => setAnswer(""),
                          );
                        }}
                      >
                        <h3>A detail needs your input</h3>
                        <p>
                          {selectedRun.result.question ||
                            "Add the missing context so Rivet can continue."}
                        </p>
                        <textarea
                          aria-label="Answer Rivet's task question"
                          rows={4}
                          maxLength={6000}
                          value={answer}
                          onChange={(event) => setAnswer(event.target.value)}
                          required
                          placeholder="Record the answer or explain what remains unknown…"
                        />
                        <small>Your answer is recorded as human input.</small>
                        <button
                          className="primary"
                          disabled={busy || !answer.trim()}
                        >
                          Save answer & resume
                          <ArrowRight size={15} />
                        </button>
                      </form>
                    )}
                    {proposals.length > 0 && (
                      <section className="agent-proposals">
                        <h3>
                          Prepared changes <span>{proposals.length}</span>
                        </h3>
                        {proposals.map((proposal) => (
                          <div
                            key={proposal.id}
                            className="agent-proposal-entry"
                          >
                            <button
                              className="agent-proposal-open"
                              aria-expanded={proposalId === proposal.id}
                              onClick={() =>
                                setProposalId(
                                  proposalId === proposal.id ? "" : proposal.id,
                                )
                              }
                            >
                              <RivetIcon name="revision" size={17} />
                              <span>
                                <strong>{proposal.title}</strong>
                                <small>
                                  {proposal.status === "pending"
                                    ? "Awaiting your review"
                                    : proposal.status}
                                </small>
                              </span>
                              <CaretDown size={15} />
                            </button>
                            {selectedProposal?.id === proposal.id && (
                              <ProposalReview
                                key={proposal.id}
                                proposal={proposal}
                                run={selectedRun}
                                w={w}
                                disabled={
                                  busy ||
                                  recordBusy ||
                                  activeStatus(selectedRun.status)
                                }
                                actor={actor}
                                openSource={openSource}
                                openComment={openComment}
                                resolve={(action, reason, expectedVersion) =>
                                  mutate(async () => {
                                    const result = await api<{
                                      record: RecordView;
                                    }>(
                                      `${base}/agent/proposals/${proposal.id}/resolve`,
                                      {
                                        action,
                                        reason,
                                        actor,
                                        expected_version: expectedVersion,
                                      },
                                    );
                                    if (result.record)
                                      qc.setQueryData(
                                        ["record", w.order.id],
                                        result.record,
                                      );
                                  })
                                }
                              />
                            )}
                          </div>
                        ))}
                      </section>
                    )}
                    {proposals.length > 0 && selectedRun.result?.summary && (
                      <details className="agent-preparation-notes">
                        <summary>Task summary</summary>
                        <p>{selectedRun.result.summary}</p>
                      </details>
                    )}
                    {showingResult && (
                      <details
                        className="agent-run-request agent-result-details"
                        key={selectedRun.id}
                      >
                        <summary>Task request & details</summary>
                        <p>{selectedGoal}</p>
                        <div className="agent-run-meta">
                          <Origin
                            mode={selectedRun.mode}
                            label={
                              selectedRun.mode === "ai"
                                ? "AI task"
                                : "Rule-based task"
                            }
                          />
                          <span
                            className={"agent-run-status " + selectedRun.status}
                          >
                            {evidenceChanged
                              ? "Evidence changed"
                              : runLabels[selectedRun.status]}
                          </span>
                        </div>
                        <p className="agent-run-time">
                          {when(selectedRun.created_at)} ·{" "}
                          {selectedRun.origin === "event"
                            ? "Triggered by new evidence"
                            : "Requested by your team"}
                        </p>
                      </details>
                    )}
                    <details
                      key={`${selectedRun.id}:${activeStatus(selectedRun.status) ? "working" : "finished"}`}
                      className="agent-completed-steps"
                      open={activeStatus(selectedRun.status) || undefined}
                    >
                      <summary>
                        {selectedRun.steps.length}{" "}
                        {activeStatus(selectedRun.status)
                          ? "steps so far"
                          : "recorded steps"}
                        <span>
                          {activeStatus(selectedRun.status)
                            ? "Live activity"
                            : "View supporting work"}
                        </span>
                      </summary>
                      <ol className="agent-step-list">
                        {selectedRun.steps.map((step) => (
                          <li
                            key={step.id}
                            className={step.status === "failed" ? "failed" : ""}
                          >
                            <span className="agent-step-marker">
                              {step.status === "failed" ? (
                                <WarningCircle size={15} />
                              ) : (
                                <Check size={15} />
                              )}
                            </span>
                            <strong>
                              {step.label || step.name.replaceAll("_", " ")}
                            </strong>
                            <p>{step.summary}</p>
                            {step.evidence_ids?.length > 0 && (
                              <EvidenceButtons
                                ids={step.evidence_ids}
                                w={w}
                                openSource={openSource}
                                openComment={openComment}
                              />
                            )}
                          </li>
                        ))}
                        {activeStatus(selectedRun.status) && (
                          <li className="is-working">
                            <span className="agent-step-marker">
                              <CircleNotch
                                size={15}
                                className="agent-spinning"
                              />
                            </span>
                            <strong>
                              {selectedRun.steps.length
                                ? "Preparing the next step"
                                : "Waiting for the worker"}
                            </strong>
                            <p>
                              Saved progress will update here. Changes remain
                              staged for review.
                            </p>
                          </li>
                        )}
                      </ol>
                    </details>
                    <div className="agent-run-actions">
                      {activeStatus(selectedRun.status) ||
                      selectedRun.status === "waiting_for_input" ? (
                        <button
                          className="text-button"
                          disabled={busy}
                          onClick={() =>
                            void mutate(() =>
                              api(
                                `${base}/agent/runs/${selectedRun.id}/cancel`,
                                {
                                  expected_version: w.version,
                                  actor,
                                  reason: "Stopped this task for human review",
                                },
                              ),
                            )
                          }
                        >
                          <X size={14} />
                          Stop task
                        </button>
                      ) : (
                        (["failed", "cancelled"].includes(selectedRun.status) ||
                          (evidenceChanged &&
                            ["waiting_for_review", "completed"].includes(
                              selectedRun.status,
                            ))) && (
                          <button
                            className="secondary"
                            disabled={busy || !!active}
                            onClick={() =>
                              void mutate(async () => {
                                const run = await api<RecordAgentRun>(
                                  `${base}/agent/runs/${selectedRun.id}/retry`,
                                  {
                                    expected_version: w.version,
                                    actor,
                                    reason: "Retry with current order evidence",
                                  },
                                );
                                qc.setQueryData<RecordAgentView>(
                                  ["record-agent", w.order.id],
                                  (current) =>
                                    current
                                      ? {
                                          ...current,
                                          runs: [
                                            run,
                                            ...current.runs.filter(
                                              (item) => item.id !== run.id,
                                            ),
                                          ],
                                        }
                                      : current,
                                );
                                setSelectedRunId(run.id);
                              })
                            }
                          >
                            <ArrowCounterClockwise size={14} />
                            {evidenceChanged
                              ? "Review current evidence"
                              : "Retry with current record"}
                          </button>
                        )
                      )}
                    </div>
                    <details className="agent-run-audit">
                      <summary>Run details & usage</summary>
                      <dl>
                        <div>
                          <dt>Engine</dt>
                          <dd>
                            {selectedRun.mode === "ai"
                              ? selectedRun.model || "Configured AI model"
                              : "Deterministic reference rules"}
                          </dd>
                        </div>
                        <div>
                          <dt>Record at start</dt>
                          <dd>
                            v{selectedRun.base_version}
                            {selectedRun.base_version !== w.version
                              ? " · record has changed"
                              : ""}
                          </dd>
                        </div>
                        <div>
                          <dt>Steps used</dt>
                          <dd>
                            {selectedRun.steps.length} /{" "}
                            {selectedRun.budget.max_steps}
                          </dd>
                        </div>
                        <div>
                          <dt>Model calls</dt>
                          <dd>{selectedRun.usage.model_calls}</dd>
                        </div>
                        <div>
                          <dt>Tokens used</dt>
                          <dd>
                            {selectedRun.usage.total_tokens.toLocaleString()}
                          </dd>
                        </div>
                      </dl>
                      {selectedRun.events.map((event) => (
                        <p key={event.id}>
                          <small>{when(event.at)}</small>
                          {event.message}
                        </p>
                      ))}
                    </details>
                  </article>
                ) : (
                  <div className="record-agent-welcome">
                    <RivetIcon name="agent" size={30} />
                    <h2>Start an order task</h2>
                    <p>
                      Give Rivet a source-grounded task. It records its steps
                      and stages proposed changes for your review.
                    </p>
                    <div className="record-agent-prompts">
                      {[
                        scopeComment
                          ? "Check this comment’s drawing reference and prepare a supported link."
                          : "Review returned comments and prepare supported drawing links.",
                        "Find unresolved comments and draft clarification questions.",
                      ].map((goal) => (
                        <button
                          key={goal}
                          disabled={busy}
                          onClick={() => start(goal)}
                        >
                          {goal}
                          <ArrowRight size={15} />
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {agent.data?.settings && (
                  <details
                    className="agent-project-controls"
                    onToggle={(event) => {
                      if (event.currentTarget.open)
                        settingsEditVersion.current = w.version;
                    }}
                  >
                    <summary>Project instructions & limits</summary>
                    <form
                      key={agent.data.settings.version}
                      onSubmit={(event) => {
                        event.preventDefault();
                        const data = new FormData(event.currentTarget);
                        void mutate(() =>
                          api(`${base}/agent/settings`, {
                            expected_version:
                              settingsEditVersion.current ?? w.version,
                            actor,
                            reason:
                              "Updated project automation instructions and limits",
                            enabled: data.get("enabled") === "on",
                            max_steps: Number(data.get("max_steps")),
                            max_output_tokens: Number(
                              data.get("max_output_tokens"),
                            ),
                            playbook: String(data.get("playbook") || ""),
                          }),
                        );
                      }}
                    >
                      <label className="agent-checkbox">
                        <input
                          name="enabled"
                          type="checkbox"
                          defaultChecked={agent.data.settings.enabled}
                        />
                        Prepare tasks when new evidence arrives
                      </label>
                      <label>
                        Project instructions
                        <textarea
                          name="playbook"
                          rows={4}
                          maxLength={4000}
                          defaultValue={agent.data.settings.playbook}
                          placeholder="Review priorities, naming conventions, and requirements for evidence…"
                        />
                      </label>
                      <div>
                        <label>
                          Maximum steps
                          <input
                            name="max_steps"
                            type="number"
                            min={2}
                            max={12}
                            defaultValue={agent.data.settings.max_steps}
                            required
                          />
                        </label>
                        <label>
                          Output token limit
                          <input
                            name="max_output_tokens"
                            type="number"
                            min={512}
                            max={3000}
                            defaultValue={agent.data.settings.max_output_tokens}
                            required
                          />
                        </label>
                      </div>
                      <button className="secondary" disabled={busy}>
                        Save instructions
                        <Check size={14} />
                      </button>
                    </form>
                  </details>
                )}
              </>
            )}
          </>
        )}
        {view === "questions" && (
          <>
            {conversation.isLoading && (
              <Busy text="Loading your saved questions…" />
            )}
            {conversation.isError && (
              <ErrorNote message={(conversation.error as Error).message} />
            )}
            {!conversation.isLoading &&
              !conversation.data?.messages.length &&
              !asking && (
                <div className="record-agent-welcome">
                  <h2>Ask about this order</h2>
                  <p>
                    Answers cite the order record. Questions are saved to your
                    account; they do not change the record.
                  </p>
                  <div className="record-agent-prompts">
                    {[
                      scopeComment
                        ? "What is this comment asking, and which drawing does it reference?"
                        : "What needs attention on this order?",
                      "What changed since the latest approval?",
                    ].map((question) => (
                      <button key={question} onClick={() => void ask(question)}>
                        {question}
                        <ArrowRight size={15} />
                      </button>
                    ))}
                  </div>
                </div>
              )}
            {conversation.data?.messages.map((message) => (
              <div className="record-agent-exchange" key={message.id}>
                <div className="record-agent-question">
                  {message.question.replace(
                    /^Context:.*\n|^Source in view:.*\n/gm,
                    "",
                  )}
                </div>
                <article className="record-agent-answer">
                  <div className="record-agent-answer-origin">
                    <Origin mode="ai" label="AI answer" />
                    <span>Answer · no record edits</span>
                  </div>
                  <div className="record-agent-prose">
                    <ReactMarkdown
                      allowedElements={[
                        "p",
                        "ul",
                        "ol",
                        "li",
                        "strong",
                        "em",
                        "code",
                        "pre",
                        "blockquote",
                        "br",
                        "h2",
                        "h3",
                        "h4",
                      ]}
                      unwrapDisallowed
                      skipHtml
                    >
                      {message.result.answer}
                    </ReactMarkdown>
                  </div>
                  <EvidenceButtons
                    ids={[
                      ...message.result.source_ids,
                      ...message.result.comment_ids,
                    ]}
                    w={w}
                    openSource={openSource}
                    openComment={openComment}
                  />
                  {message.result.change_ids.map((changeId) => (
                    <button
                      key={changeId}
                      className="text-button"
                      onClick={() => openChanges(changeId)}
                    >
                      {w.changes.find((change) => change.id === changeId)
                        ?.title || "View cited change"}
                      <ArrowUpRight size={14} />
                    </button>
                  ))}
                  <small>
                    Record v{message.result.version}
                    {message.result.version !== w.version
                      ? " · updated since this answer"
                      : ""}
                  </small>
                </article>
              </div>
            ))}
            {asking && (
              <div ref={lastQuestion} className="record-agent-exchange">
                <div className="record-agent-question">{activeQuestion}</div>
                <div className="record-agent-working" role="status">
                  <CircleNotch size={15} className="agent-spinning" />
                  Reading retrieved order evidence…
                </div>
              </div>
            )}
          </>
        )}
        {view === "activity" && (
          <>
            <div className="agent-activity-intro">
              <h2>Recorded activity</h2>
              <p>
                Imports, automatic checks, AI preparation, and human decisions
                stay distinguishable.
              </p>
            </div>
            {w.documents
              .filter((document) =>
                ["queued", "failed"].includes(document.state),
              )
              .map((document) => (
                <button
                  className="agent-document-state"
                  key={document.id}
                  onClick={() => openSource(document.id)}
                >
                  <RivetIcon name="drawing" size={17} />
                  <span>
                    {document.name}
                    <small>
                      {document.state === "queued"
                        ? "Import queued · automatic processing"
                        : "Import failed · manual review needed"}
                    </small>
                  </span>
                  <ArrowUpRight size={14} />
                </button>
              ))}
            <ol className="agent-activity-list">
              {w.events.slice(0, 60).map((event) => (
                <li key={event.id}>
                  <span className="agent-activity-dot" />
                  <strong>{event.summary}</strong>
                  <div>
                    <ActivityAttribution
                      actor={event.actor}
                      kind={event.actor_kind}
                      proposalOrigin={event.proposal_origin}
                    />
                    <time>{when(event.at)}</time>
                  </div>
                  {event.reason && <p>{event.reason}</p>}
                </li>
              ))}
            </ol>
            {!w.events.length && (
              <p className="agent-empty-note">
                Activity will appear here as documents and decisions are
                recorded.
              </p>
            )}
            {(w.coordination?.activity ?? []).filter(
              (item) => item.undoable && !item.undone_at,
            ).length > 0 && (
              <details className="agent-run-audit">
                <summary>Reversible automatic actions</summary>
                {w.coordination?.activity
                  .filter((item) => item.undoable && !item.undone_at)
                  .map((item) => (
                    <div className="agent-reversible-action" key={item.id}>
                      <span>{item.title}</span>
                      <button
                        className="text-button"
                        disabled={recordBusy}
                        onClick={() =>
                          void save(`/coordination/activity/${item.id}/undo`, {
                            expected_version: w.version,
                            actor,
                            reason: `Undo: ${item.title}`,
                          })
                        }
                      >
                        Undo
                        <ArrowCounterClockwise size={13} />
                      </button>
                    </div>
                  ))}
              </details>
            )}
            <button className="text-button" onClick={openWork}>
              Open readiness & team tasks
              <ArrowRight size={14} />
            </button>
          </>
        )}
      </div>
      {view !== "activity" && (
        <footer
          className={
            "record-agent-composer" +
            (showingResult && !composerExpanded ? " is-collapsed" : "")
          }
        >
          {showingResult && !composerExpanded ? (
            <button
              className="text-button agent-new-task"
              onClick={() => {
                setComposerExpanded(true);
                requestAnimationFrame(() => input.current?.focus());
              }}
            >
              <Plus size={15} /> New task
            </button>
          ) : (
            <>
              {showingResult && (
                <button
                  className="text-button agent-composer-close"
                  onClick={() => setComposerExpanded(false)}
                >
                  Close new task <X size={13} />
                </button>
              )}
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  if (view === "questions") void ask(prompt);
                  else start(prompt);
                }}
              >
                <label
                  className="sr-only"
                  htmlFor={`agent-command-${w.order.id}`}
                >
                  {view === "questions"
                    ? "Ask Rivet about this order"
                    : "Give Rivet a task"}
                </label>
                <textarea
                  ref={input}
                  id={`agent-command-${w.order.id}`}
                  rows={3}
                  value={prompt}
                  maxLength={view === "questions" ? 4500 : 2500}
                  onChange={(event) => setPrompt(event.target.value)}
                  placeholder={
                    view === "questions"
                      ? "Ask about the selected evidence…"
                      : "Prepare a review, trace a reference, draft a follow-up…"
                  }
                  onKeyDown={(event) => {
                    if (
                      (event.metaKey || event.ctrlKey) &&
                      event.key === "Enter"
                    ) {
                      event.preventDefault();
                      if (view === "questions") void ask(prompt);
                      else start(prompt);
                    }
                  }}
                />
                <div>
                  <span>
                    {view === "questions"
                      ? "Read-only question"
                      : active
                        ? "A task is running"
                        : "Proposed changes need review"}
                  </span>
                  <button
                    className="record-agent-send"
                    disabled={
                      !prompt.trim() ||
                      busy ||
                      asking ||
                      (view === "runs" && !!active)
                    }
                    aria-label={
                      view === "questions"
                        ? "Send question"
                        : "Start Rivet task"
                    }
                  >
                    {busy || asking ? (
                      <CircleNotch className="agent-spinning" size={17} />
                    ) : (
                      <ArrowRight size={18} />
                    )}
                  </button>
                </div>
              </form>
            </>
          )}
        </footer>
      )}
    </section>
  );
}

function Origin({ mode, label }: { mode: "ai" | "rules"; label?: string }) {
  return (
    <span className={"agent-origin " + mode}>
      <RivetIcon name={mode === "ai" ? "agent" : "review"} size={13} />
      {label || (mode === "ai" ? "AI prepared" : "Rule-based check")}
    </span>
  );
}

function EvidenceButtons({
  ids,
  w,
  openSource,
  openComment,
}: {
  ids: string[];
  w: RecordView;
  openSource: (id: string) => void;
  openComment: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const valid = [...new Set(ids)].filter(
    (id) =>
      w.sources.some((item) => item.id === id) ||
      w.comments.some((item) => item.id === id) ||
      w.documents.some((item) => item.id === id),
  );
  const shown = expanded ? valid : valid.slice(0, 3);
  return (
    <div className="record-agent-citations">
      {shown.map((id) => {
        const source = w.sources.find((item) => item.id === id);
        const comment = w.comments.find((item) => item.id === id);
        const document = w.documents.find(
          (item) => item.id === (source?.document_id || id),
        );
        const label = comment
          ? `Comment ${comment.number}`
          : `${source?.location.page ? `p. ${source.location.page} · ` : ""}${document?.name || "Source evidence"}`;
        return (
          <button
            key={id}
            title={label}
            onClick={() => (comment ? openComment(id) : openSource(id))}
          >
            <RivetIcon name={comment ? "comment" : "evidence"} size={13} />
            <span>{label}</span>
            <ArrowUpRight size={12} />
          </button>
        );
      })}
      {valid.length > 3 && (
        <button
          className="agent-more-citations"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded
            ? "Fewer references"
            : `+ ${valid.length - 3} more references`}
        </button>
      )}
    </div>
  );
}

function ProposalReview({
  proposal,
  run,
  w,
  disabled,
  actor,
  openSource,
  openComment,
  resolve,
}: {
  proposal: RecordAgentProposal;
  run: RecordAgentRun;
  w: RecordView;
  disabled: boolean;
  actor: string;
  openSource: (id: string) => void;
  openComment: (id: string) => void;
  resolve: (
    action: "accept" | "reject",
    reason: string,
    expectedVersion: number,
  ) => Promise<boolean>;
}) {
  const [reviewVersion] = useState(w.version);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const stale =
    proposal.status === "stale" ||
    proposal.base_version !== w.version ||
    reviewVersion !== w.version;
  return (
    <div className="agent-proposal-review">
      <div className="agent-proposal-meta">
        <Origin mode={proposal.origin} />
        <span>Record v{proposal.base_version}</span>
      </div>
      <div className="agent-domain-changes">
        {proposal.operations.map((operation, index) => (
          <OperationReview
            key={index}
            operation={operation}
            proposal={proposal}
            w={w}
            openSource={openSource}
            openComment={openComment}
          />
        ))}
      </div>
      <details className="agent-preparation-notes">
        <summary>Why these changes were prepared</summary>
        <p>{proposal.summary}</p>
      </details>
      <EvidenceButtons
        ids={proposal.evidence_ids}
        w={w}
        openSource={openSource}
        openComment={openComment}
      />
      {proposal.status === "pending" ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void resolve("accept", reason.trim(), reviewVersion);
          }}
        >
          {stale && (
            <p className="agent-proposal-stale">
              <WarningCircle size={15} />
              The record changed. Start a fresh task before accepting this
              proposal.
            </p>
          )}
          <p className="agent-accept-scope">
            Acceptance saves these internal record changes. It does not send a
            message, create an engineering response, or approve the package.
          </p>
          <label className="agent-checkbox">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            I reviewed the proposed changes and supporting evidence.
          </label>
          <label>
            Review note
            <textarea
              rows={2}
              required
              minLength={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Why these changes are correct, or why they should be dismissed"
            />
          </label>
          <small>
            Decision recorded by <ActorName actor={actor} />
          </small>
          <div className="agent-proposal-actions">
            <button
              className="secondary"
              type="button"
              disabled={disabled || reason.trim().length < 3}
              onClick={() =>
                void resolve("reject", reason.trim(), reviewVersion)
              }
            >
              Dismiss
            </button>
            <button
              className="primary"
              disabled={
                disabled || stale || !confirmed || reason.trim().length < 3
              }
            >
              Accept reviewed changes
              <Check size={15} />
            </button>
          </div>
        </form>
      ) : (
        <p className="agent-proposal-outcome">
          {proposal.status === "accepted"
            ? "Accepted by a person and saved to the record."
            : proposal.status === "rejected"
              ? "Dismissed by a person. The record was not changed."
              : "This proposal is out of date. Prepare a fresh task against the current record."}
        </p>
      )}
    </div>
  );
}
function OperationReview({
  operation,
  proposal,
  w,
  openSource,
  openComment,
}: {
  operation: RecordAgentProposal["operations"][number];
  proposal: RecordAgentProposal;
  w: RecordView;
  openSource: (id: string) => void;
  openComment: (id: string) => void;
}) {
  const comment = w.comments.find((item) => item.id === operation.comment_id);
  const before =
    proposal.before &&
    typeof proposal.before === "object" &&
    !Array.isArray(proposal.before)
      ? (proposal.before as Record<string, Record<string, unknown>>)[
          operation.comment_id || ""
        ]
      : undefined;
  const oldLink =
    typeof before?.target_source_id === "string" ? before.target_source_id : "";
  const isLink = operation.type === "link_source";
  const isClarification = operation.type === "draft_clarification";
  const label = isLink
    ? "Drawing reference"
    : isClarification
      ? "Clarification draft"
      : operation.type === "create_task"
        ? "Team task"
        : "Summary draft";
  return (
    <section className="agent-domain-change">
      <div className="agent-domain-change-heading">
        <RivetIcon
          name={
            isLink
              ? "drawing"
              : operation.type === "create_task"
                ? "review"
                : "handoff"
          }
          size={17}
        />
        <h4>{label}</h4>
        {comment && (
          <button
            className="text-button"
            onClick={() => openComment(comment.id)}
          >
            Comment {comment.number}
            <ArrowUpRight size={12} />
          </button>
        )}
      </div>
      <div className="agent-proposed-output">
        {isLink ? (
          <>
            <span className="agent-output-label">Proposed reference</span>
            <EvidenceButtons
              ids={
                operation.target_source_id ? [operation.target_source_id] : []
              }
              w={w}
              openSource={openSource}
              openComment={openComment}
            />
          </>
        ) : (
          <>
            {operation.title && <strong>{operation.title}</strong>}
            <p>{operation.text}</p>
            {operation.type === "create_task" && operation.role && (
              <small>
                Team:{" "}
                {{
                  pm: "Project management",
                  drafting: "Drafting",
                  production: "Production",
                  commercial: "Commercial",
                }[operation.role] || operation.role}
              </small>
            )}
          </>
        )}
      </div>
      <details className="agent-existing-record">
        <summary>
          {isLink
            ? "Current reference"
            : isClarification && comment
              ? "Original customer comment"
              : "Current record"}
        </summary>
        {isLink ? (
          oldLink ? (
            <EvidenceButtons
              ids={[oldLink]}
              w={w}
              openSource={openSource}
              openComment={openComment}
            />
          ) : (
            <p>No drawing reference linked.</p>
          )
        ) : isClarification && comment ? (
          <p>{typeof before?.text === "string" ? before.text : comment.text}</p>
        ) : (
          <p>
            {operation.type === "create_task"
              ? "This task has not been added to the record."
              : "This summary has not been saved to the record."}
          </p>
        )}
      </details>
    </section>
  );
}
