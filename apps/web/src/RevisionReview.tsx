import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowUpRight,
  DownloadSimple,
  MagnifyingGlass,
  Plus,
  WarningCircle,
} from "@phosphor-icons/react";
import { api, apiFetch } from "./api";
import { ErrorNote, RivetIcon } from "./ui";
import { CommentAttribution } from "./RecordAttribution";
import { ReviewBrief } from "./Integrations";
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
}: {
  id: string;
  number?: string;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const outputs = [
    { format: "response_pdf", name: "Response letter", extension: "pdf" },
    { format: "xlsx", name: "Excel comment log", extension: "xlsx" },
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
        <ReviewBrief id={id} compact />
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
  const [revision, setRevision] = useState("all");
  const agent = useQuery({
    queryKey: ["record-agent", w.order.id],
    queryFn: () => api<RecordAgentView>(`/orders/${w.order.id}/record/agent`),
    refetchInterval: 10000,
  });
  const flags = w.revision_review?.flags ?? [];
  const ready = (agent.data?.proposals ?? []).filter(
    (proposal) =>
      proposal.status === "pending" &&
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
  const latest =
    w.revision_review?.latest_revision ||
    w.comments.filter((comment) => comment.revision).at(-1)?.revision ||
    w.documents.filter((document) => document.revision_label).at(-1)
      ?.revision_label;
  const latestComments = latest
    ? w.comments.filter((comment) => comment.revision === latest)
    : w.comments;
  const pending = latestComments.filter(
    (comment) =>
      !["awaiting_customer", "resolved"].includes(commentWorkflow(comment)),
  ).length;
  const earlierOpen = latest
    ? w.comments.filter(
        (comment) =>
          comment.revision !== latest && commentWorkflow(comment) !== "resolved",
      ).length
    : 0;
  const riskCount = new Set(
    flags.filter((flag) => flag.kind !== "new").map((flag) => flag.comment_id),
  ).size;
  const visible = w.comments.filter((comment) => {
    const commentFlags = flags.filter((flag) => flag.comment_id === comment.id);
    return (
      (filter === "all" ||
        (filter === "needs_response" &&
          commentWorkflow(comment) === "needs_response") ||
        (filter === "needs_review" && !comment.reviewed) ||
        commentFlags.some((flag) => flag.kind === filter)) &&
      (revision === "all" || comment.revision === revision) &&
      `${comment.text} ${comment.number} ${comment.author} ${comment.response}`
        .toLowerCase()
        .includes(search.toLowerCase())
    );
  });
  return (
    <div className="revision-review">
      <section className="revision-brief" aria-label="Revision review summary">
        <div className="revision-brief-copy">
          <span className="revision-kicker">
            <RivetIcon name="inbox" size={17} />
            {latest ? `Revision ${latest}` : "Returned drawings"}
          </span>
          <h2>
            {w.comments.length
              ? `${latestComments.length} ${latestComments.length === 1 ? "comment" : "comments"}${latest ? " in this return" : ""}. ${pending} ${pending === 1 ? "needs" : "need"} action.`
              : "Add a returned drawing to start."}
          </h2>
          <p>
            {earlierOpen > 0 &&
              `${earlierOpen} earlier ${earlierOpen === 1 ? "comment remains" : "comments remain"} open. `}
            {w.comments.length
              ? riskCount
                ? `${riskCount} ${riskCount === 1 ? "comment carries" : "comments carry"} revision flags. Open a flag to compare its original evidence.`
                : "Review the imported comments, record your responses, and download the handoff."
              : "Drop in the PDF or saved email. Rivet keeps each comment connected to its original source."}
          </p>
        </div>
        <div className="revision-agent-state">
          <RivetIcon name="agent" size={26} />
          <div>
            <strong>
              {working
                ? "Rivet is working"
                : ready.length
                  ? `${ready.length} prepared ${ready.length === 1 ? "result" : "results"}`
                  : "Rivet assistant"}
            </strong>
            <span>
              {working
                ? "Task progress and evidence are available."
                : ready.length
                  ? "Review the evidence before applying a result."
                  : "Check references or prepare a clarification."}
            </span>
          </div>
          <button
            className="text-button"
            onClick={() => openAgent(ready[0]?.id)}
          >
            {ready.length ? "Review work" : "Open Rivet"}
            <ArrowRight size={16} />
          </button>
        </div>
      </section>
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
                onClick={() => setFilter(filter === kind ? "all" : kind)}
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
          <small>Automatic record checks · confirm with the source</small>
        </section>
      )}
      <section className="revision-log" aria-label="Comment log">
        <div className="revision-log-toolbar">
          <div
            className="revision-log-filters"
            role="group"
            aria-label="Filter comments"
          >
            {[
              ["all", "All comments"],
              ["needs_response", "Needs response"],
              ["needs_review", "Needs review"],
            ].map(([value, label]) => (
              <button
                key={value}
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
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
            <select
              aria-label="Revision"
              value={revision}
              onChange={(event) => setRevision(event.target.value)}
            >
              <option value="all">All revisions</option>
              {[...new Set(w.comments.map((comment) => comment.revision))]
                .filter(Boolean)
                .map((value) => (
                  <option key={value} value={value}>
                    Rev {value}
                  </option>
                ))}
            </select>
          </div>
        </div>
        {flagOrder.includes(filter as RevisionFlagKind) && (
          <div className="revision-active-filter">
            Showing {flagLabels[filter as RevisionFlagKind].toLowerCase()}
            <button className="text-button" onClick={() => setFilter("all")}>
              Clear filter
            </button>
          </div>
        )}
        {visible.length ? (
          <div className="revision-table-scroll">
            <table className="revision-table">
              <thead>
                <tr>
                  <th>Ref.</th>
                  <th>Customer comment</th>
                  <th>Revision check</th>
                  <th>Status</th>
                  <th>
                    <span className="sr-only">Open</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((comment) => {
                  const commentFlags = flags
                    .filter((flag) => flag.comment_id === comment.id)
                    .sort(
                      (a, b) =>
                        flagOrder.indexOf(a.kind) - flagOrder.indexOf(b.kind),
                    );
                  const workflow = commentWorkflow(comment);
                  return (
                    <tr key={comment.id}>
                      <td>
                        <span className="revision-ref">
                          {comment.number || "—"}
                        </span>
                        <small>Rev {comment.revision || "—"}</small>
                      </td>
                      <td>
                        <button
                          className="revision-comment-link"
                          onClick={() => openComment(comment.id)}
                        >
                          {comment.text}
                        </button>
                        <div className="revision-comment-meta">
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
                        {comment.response && (
                          <p className="revision-recorded-response">
                            <span>Recorded response</span>
                            {comment.response}
                          </p>
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
                            <button
                              className="revision-evidence-link"
                              onClick={() => openComment(comment.id)}
                            >
                              View evidence <ArrowUpRight size={12} />
                            </button>
                          </div>
                        ) : (
                          <span className="revision-no-flag">
                            {w.revision_review ? "No flag" : "Not compared"}
                          </span>
                        )}
                      </td>
                      <td>
                        <span className={`revision-workflow ${workflow}`}>
                          <i />
                          {workflowLabels[workflow]}
                        </span>
                      </td>
                      <td>
                        <button
                          className="icon-button"
                          aria-label={`Review comment ${comment.number}`}
                          onClick={() => openComment(comment.id)}
                        >
                          <ArrowRight size={18} />
                        </button>
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
      </section>
      <ReviewOutputs id={w.order.id} number={w.order.number} />
      <p className="revision-boundary">
        <WarningCircle size={15} />
        Flags identify records to review. They do not verify that a drawing is
        technically correct or that a change is outside contract scope.
      </p>
    </div>
  );
}
