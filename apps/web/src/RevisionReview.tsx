import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowUpRight,
  DownloadSimple,
  MagnifyingGlass,
  Plus,
  WarningCircle,
  CaretDown,
} from "@phosphor-icons/react";
import { api, apiFetch } from "./api";
import { ErrorNote, RivetIcon } from "./ui";
import { CommentAttribution } from "./RecordAttribution";
import { ReviewBrief } from "./Integrations";
import { OrderPreparedReply } from "./OrderPreparedReply";
import { TranscriptionCorrection } from "./TranscriptionCorrection";
import type {
  RecordComment,
  RecordView,
  RevisionFlagKind,
} from "./record-types";
import type { RecordAgentView } from "./record-agent-types";

const flagLabels: Record<RevisionFlagKind, string> = {
  new: "New comment",
  repeated: "Repeated comment",
  unresolved: "Prior item still open",
  late_change: "After record approval",
  scope_change: "Possible scope change",
};
const flagOrder: RevisionFlagKind[] = [
  "late_change",
  "scope_change",
  "unresolved",
  "repeated",
  "new",
];
export const workflowLabels = {
  needs_review: "Needs review",
  needs_response: "Needs response",
  sent_to_drafting: "Sent to drafting",
  awaiting_customer: "Awaiting customer",
  resolved: "Resolved",
};
export function commentWorkflow(comment: RecordComment) {
  return (
    comment.workflow_status ||
    (!comment.reviewed
      ? "needs_review"
      : comment.status === "closed"
        ? "resolved"
        : comment.status === "responded"
          ? "awaiting_customer"
          : "needs_response")
  );
}

export function ReviewOutputs({
  id,
  number = "",
  includeBrief = true,
}: {
  id: string;
  number?: string;
  includeBrief?: boolean;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const outputs = [
    { format: "response_pdf", name: "Response letter", extension: "pdf" },
    { format: "xlsx", name: "Excel comment log", extension: "xlsx" },
    { format: "docx", name: "Word comment matrix", extension: "docx" },
    { format: "pdf", name: "PDF comment log", extension: "pdf" },
    { format: "drafting_xlsx", name: "Drafting list", extension: "xlsx" },
  ];
  async function download(format: string, extension: string) {
    setBusy(format);
    setError("");
    try {
      const response = await apiFetch(
        `/api/orders/${id}/record/export?format=${format}`,
      );
      if (!response.ok) {
        let message = "Could not prepare this download. Try again.";
        try {
          const body = await response.json();
          if (typeof body.detail === "string") message = body.detail;
        } catch {
          /* The server may return a non-JSON error. */
        }
        throw new Error(message);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      const label =
        outputs
          .find((output) => output.format === format)
          ?.name.toLowerCase()
          .replaceAll(" ", "-") || format;
      const prefix = number.replace(/[^a-zA-Z0-9.-]/g, "-").slice(0, 80);
      link.download = `${prefix ? prefix + "-" : "rivet-"}${label}.${extension}`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="revision-outputs" aria-label="Download order outputs">
      <div>
        <RivetIcon name="handoff" size={20} />
        <span>
          <strong>Order outputs</strong>
          <small>Download the current record. Review before sending.</small>
        </span>
      </div>
      <div className="revision-output-actions">
        {outputs.map((output) => (
          <button
            className="secondary"
            key={output.format}
            disabled={!!busy}
            onClick={() => void download(output.format, output.extension)}
          >
            <DownloadSimple size={16} />
            {busy === output.format ? "Preparing…" : output.name}
          </button>
        ))}
        {includeBrief && <ReviewBrief id={id} compact />}
      </div>
      {error && <ErrorNote message={error} />}
    </section>
  );
}

export function RevisionReview({
  w,
  openComment,
  openAgent,
  addDocuments,
  addComment,
  openReadiness,
  openSource,
}: {
  w: RecordView;
  openComment: (id: string) => void;
  openAgent: (proposalId?: string) => void;
  addDocuments: () => void;
  addComment: () => void;
  openReadiness: () => void;
  openSource: (id: string, commentId?: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [revision, setRevision] = useState("latest");
  const agent = useQuery({
    queryKey: ["record-agent", w.order.id],
    queryFn: () => api<RecordAgentView>(`/orders/${w.order.id}/record/agent`),
    refetchInterval: 10000,
  });
  const flags = w.revision_review?.flags ?? [];
  const ready = (agent.data?.proposals ?? []).filter(
    (proposal) =>
      ["pending", "prepared"].includes(proposal.status) &&
      proposal.base_version === w.version &&
      !agent.data?.runs.some(
        (run) =>
          run.id === proposal.run_id &&
          ["queued", "running"].includes(run.status),
      ),
  );
  const working = agent.data?.runs.some((run) =>
    ["queued", "running"].includes(run.status),
  );
  const readyDrafts = ready.reduce(
    (total, proposal) => total + proposal.operations.length,
    0,
  );
  const latest =
    w.revision_review?.latest_revision ||
    w.comments.filter((comment) => comment.revision).at(-1)?.revision ||
    w.documents.filter((document) => document.revision_label).at(-1)
      ?.revision_label;
  const latestComments = latest
    ? w.comments.filter((comment) => comment.revision === latest)
    : w.comments;
  const revisions = [
    ...new Set(w.comments.map((comment) => comment.revision)),
  ].filter(Boolean);
  const earlierOpen = latest
    ? w.comments.filter(
        (comment) =>
          comment.revision !== latest &&
          commentWorkflow(comment) !== "resolved",
      ).length
    : 0;
  const selectedRevision = revision === "latest" ? latest : revision;
  const visible = w.comments.filter((comment) => {
    const commentFlags = flags.filter((flag) => flag.comment_id === comment.id);
    return (
      (filter === "all" ||
        (filter === "earlier_open" &&
          comment.revision !== latest &&
          commentWorkflow(comment) !== "resolved") ||
        (filter === "needs_response" &&
          commentWorkflow(comment) === "needs_response") ||
        (filter === "needs_review" && !comment.reviewed) ||
        commentFlags.some((flag) => flag.kind === filter)) &&
      (selectedRevision === "all" ||
        !selectedRevision ||
        comment.revision === selectedRevision) &&
      `${comment.text} ${comment.number} ${comment.author} ${comment.response}`
        .toLowerCase()
        .includes(search.toLowerCase())
    );
  });
  return (
    <div className="revision-review">
      <OrderPreparedReply orderId={w.order.id} />
      <section className="revision-brief" aria-label="Revision review summary">
        <div className="revision-brief-copy">
          <span className="revision-kicker">
            <RivetIcon name="inbox" size={17} />
            {latest ? `Revision ${latest}` : "Returned drawings"}
            {w.comments.length > 0 && ` · ${latestComments.length} comments`}
          </span>
          <h2>
            {w.comments.length
              ? "Comment log"
              : "Add a returned drawing to start."}
          </h2>
          {!w.comments.length && (
            <p>
              Drop in the PDF or saved email. Rivet keeps each comment connected
              to its original source.
            </p>
          )}
        </div>
        {!w.comments.length && (
          <button className="secondary" onClick={addDocuments}>
            Add returned drawing <Plus size={17} />
          </button>
        )}
      </section>
      {(working || ready.length > 0) && (
        <div className="revision-prepared-work">
          <RivetIcon name="agent" size={26} />
          <div>
            <strong>
              {working
                ? "Rivet is working"
                : ready.length
                  ? "Rivet has prepared the supporting work"
                  : "Rivet work"}
            </strong>
            <span>
              {working
                ? "Task progress and evidence are available."
                : ready.length
                  ? `${readyDrafts} source-linked ${readyDrafts === 1 ? "result" : "results"} saved with this order.`
                  : "Check references or prepare a clarification."}
            </span>
          </div>
          <button className="secondary" onClick={() => openAgent(ready[0]?.id)}>
            {ready.length ? "See what’s done" : "View progress"}
            <ArrowRight size={16} />
          </button>
        </div>
      )}
      {flags.some((flag) => flag.kind !== "new") && (
        <section className="revision-risk-strip" aria-label="Revision flags">
          {flagOrder
            .filter(
              (kind) =>
                kind !== "new" && flags.some((flag) => flag.kind === kind),
            )
            .map((kind) => (
              <button
                key={kind}
                className={filter === kind ? "selected" : ""}
                aria-pressed={filter === kind}
                onClick={() => {
                  setFilter(filter === kind ? "all" : kind);
                  setRevision(filter === kind ? "latest" : "all");
                }}
              >
                <span className={`revision-risk-mark ${kind}`} />
                <strong>
                  {
                    new Set(
                      flags
                        .filter((flag) => flag.kind === kind)
                        .map((flag) => flag.comment_id),
                    ).size
                  }
                </strong>
                <span>{flagLabels[kind]}</span>
                <ArrowRight size={14} />
              </button>
            ))}
          <small>Automatic checks · review the evidence</small>
        </section>
      )}
      <section className="revision-log" aria-label="Comment log">
        <div className="revision-log-toolbar">
          {revisions.length > 1 && (
            <div
              className="revision-log-filters"
              role="group"
              aria-label="Filter comments"
            >
              {[
                ["latest", "Latest return"],
                [
                  "earlier_open",
                  `Earlier open${earlierOpen ? ` · ${earlierOpen}` : ""}`,
                ],
                ["all", "All comments"],
              ].map(([value, label]) => (
                <button
                  key={value}
                  aria-pressed={
                    value === "earlier_open"
                      ? filter === value
                      : filter === "all" && revision === value
                  }
                  onClick={() => {
                    setFilter(value === "earlier_open" ? value : "all");
                    setRevision(value === "earlier_open" ? "all" : value);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          <div className="revision-log-tools">
            <label className="revision-log-search">
              <MagnifyingGlass size={17} />
              <input
                aria-label="Search comment log"
                placeholder="Find a comment…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            {revisions.length > 1 && (
              <div className="revision-select">
                <select
                  aria-label="Revision"
                  value={revision}
                  onChange={(event) => {
                    setRevision(event.target.value);
                    setFilter("all");
                  }}
                >
                  <option value="latest">
                    Latest{latest ? ` · ${latest}` : " return"}
                  </option>
                  <option value="all">All revisions</option>
                  {revisions.map((value) => (
                    <option key={value} value={value}>
                      Rev {value}
                    </option>
                  ))}
                </select>
                <CaretDown size={15} aria-hidden="true" />
              </div>
            )}
          </div>
        </div>
        {flagOrder.includes(filter as RevisionFlagKind) && (
          <div className="revision-active-filter">
            Showing {flagLabels[filter as RevisionFlagKind].toLowerCase()}
            <button
              className="text-button"
              onClick={() => {
                setFilter("all");
                setRevision("latest");
              }}
            >
              Clear filter
            </button>
          </div>
        )}
        {visible.length ? (
          <div className="revision-table-scroll">
            <table className="revision-table">
              <thead>
                <tr>
                  <th>Log ID</th>
                  <th>Customer comment</th>
                  <th>Attention</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((comment) => {
                  const commentFlags = flags
                    .filter(
                      (flag) =>
                        flag.comment_id === comment.id && flag.kind !== "new",
                    )
                    .sort(
                      (a, b) =>
                        flagOrder.indexOf(a.kind) - flagOrder.indexOf(b.kind),
                    );
                  const workflow = commentWorkflow(comment);
                  const captured =
                    workflow === "needs_review" &&
                    !comment.reviewed &&
                    !["manual", "unreadable area"].includes(comment.origin);
                  return (
                    <tr key={comment.id}>
                      <td>
                        <span className="revision-ref">
                          {comment.log_reference || comment.number || "—"}
                        </span>
                        <small>Rev {comment.revision || "—"}</small>
                        <small title="The number recorded in the original source">
                          Original {comment.number || "—"}
                        </small>
                      </td>
                      <td>
                        <button
                          className="revision-comment-link"
                          onClick={() => openComment(comment.id)}
                        >
                          {comment.text}
                        </button>
                        <div className="revision-comment-meta">
                          <button
                            className="revision-row-review"
                            aria-label={`Open comment ${comment.log_reference || comment.number}`}
                            onClick={() => openComment(comment.id)}
                          >
                            Open <ArrowRight size={15} />
                          </button>
                          <CommentAttribution comment={comment} compact />
                          <span>{comment.author || "Author not recorded"}</span>
                          <button
                            onClick={() =>
                              openSource(
                                comment.source_ids[0] || comment.document_id,
                                comment.id,
                              )
                            }
                            disabled={
                              !comment.document_id && !comment.source_ids.length
                            }
                          >
                            {comment.source_page
                              ? `Source p. ${comment.source_page}`
                              : "Open source"}
                            <ArrowUpRight size={12} />
                          </button>
                        </div>
                        <TranscriptionCorrection
                          key={`${w.order.id}:${comment.id}`}
                          w={w}
                          comment={comment}
                        />
                        {comment.response && (
                          <details className="revision-recorded-response">
                            <summary>Recorded response</summary>
                            <p>{comment.response}</p>
                          </details>
                        )}
                      </td>
                      <td>
                        {commentFlags.length ? (
                          <div className="revision-row-flags">
                            {commentFlags.map((flag) => (
                              <button
                                key={flag.id}
                                className={`revision-flag ${flag.kind}`}
                                onClick={() => openComment(comment.id)}
                                title={flag.detail}
                                aria-label={`${flagLabels[flag.kind]}. ${flag.detail}`}
                              >
                                {flagLabels[flag.kind]}
                              </button>
                            ))}
                          </div>
                        ) : (
                          <span className="revision-no-flag">
                            {w.revision_review ? "—" : "Not compared"}
                          </span>
                        )}
                      </td>
                      <td>
                        <span
                          className={`revision-workflow ${captured ? "recorded" : workflow}`}
                          title={
                            captured
                              ? "Captured from the source. This does not mark the comment as human-reviewed."
                              : undefined
                          }
                        >
                          <i />
                          {captured ? "Recorded" : workflowLabels[workflow]}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="revision-log-empty">
            <RivetIcon name="comment" size={30} />
            <h3>
              {w.comments.length
                ? "No comments match this view"
                : w.documents.some((document) => document.state === "queued")
                  ? "Rivet is reading the documents"
                  : "Your comment log starts here"}
            </h3>
            <p>
              {w.comments.length
                ? "Clear the filters to return to the full log."
                : "Add a marked-up PDF or EML email. Scanned or unclear marks are kept for a person to review."}
            </p>
            <button
              className="secondary"
              onClick={
                w.comments.length
                  ? () => {
                      setFilter("all");
                      setRevision("all");
                      setSearch("");
                    }
                  : addDocuments
              }
            >
              {w.comments.length ? "Clear filters" : "Add returned drawing"}
            </button>
          </div>
        )}
        <footer className="revision-log-footer">
          <span>
            {visible.length} of {w.comments.length} comments
          </span>
          <div>
            <button className="text-button" onClick={addComment}>
              <Plus size={15} />
              Add comment
            </button>
            <button className="text-button" onClick={openReadiness}>
              Team assignments
              <ArrowUpRight size={14} />
            </button>
          </div>
        </footer>
        {!!w.email_context?.length && (
          <p className="revision-context-note">
            {w.email_context.length} email cover{" "}
            {w.email_context.length === 1 ? "note is" : "notes are"} kept with
            the source files, outside the comment log.
          </p>
        )}
      </section>
      <details className="record-file-details">
        <summary>
          Download response letter, comment log, or drafting list
        </summary>
        <ReviewOutputs id={w.order.id} number={w.order.number} />
      </details>
      <p className="revision-boundary">
        <WarningCircle size={15} />
        Flags identify records to review. They do not verify that a drawing is
        technically correct or that a change is outside contract scope.
      </p>
    </div>
  );
}
