import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  CheckCircle,
  List,
  MagnifyingGlass,
  Plus,
  X,
} from "@phosphor-icons/react";
import { Empty, RivetIcon } from "./ui";
import { OrderEvidence, type EvidencePosition } from "./OrderEvidence";
import { CommentAttribution } from "./RecordAttribution";
import {
  RecordAgent,
  type AgentWorkState,
  type AgentTaskRequest,
} from "./RecordAgent";
import type { RecordView, RecordComment } from "./record-types";
import { DRAWING_REFERENCE_GOAL } from "./record-agent-intents";

type Props = {
  w: RecordView;
  selectedId: string | null;
  sourceId: string;
  sourceRequest: number;
  openSource: (id: string) => void;
  selectComment: (id: string) => void;
  agentOpen: boolean;
  setAgentOpen: (open: boolean) => void;
  addDocuments: () => void;
  openComments: () => void;
  openChanges: (changeId?: string) => void;
  openReadiness: () => void;
  save: (path: string, body: unknown) => Promise<boolean>;
  busy: boolean;
  error: string;
  renderEditor: (close: () => void) => ReactNode;
};

export function RecordWorkbench({
  w,
  selectedId,
  sourceId,
  sourceRequest,
  openSource,
  selectComment,
  agentOpen,
  setAgentOpen,
  addDocuments,
  openComments,
  openChanges,
  openReadiness,
  save,
  busy,
  error,
  renderEditor,
}: Props) {
  const [queueOpen, setQueueOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [taskRequest, setTaskRequest] = useState<AgentTaskRequest | null>(null);
  const [composerRequest, setComposerRequest] = useState(0);
  const [agentWork, setAgentWork] = useState<AgentWorkState>({
    ready: 0,
    needsInput: 0,
    working: false,
  });
  const [query, setQuery] = useState("");
  const [queueFilter, setQueueFilter] = useState<"review" | "open" | "all">(
    "review",
  );
  const positions = useRef(new Map<string, EvidencePosition>());
  const inspector = useRef<HTMLElement>(null);
  const comment = w.comments.find((item) => item.id === selectedId);
  const commentIndex = w.comments.findIndex((item) => item.id === selectedId);
  const effectiveSourceId =
    sourceId ||
    comment?.target_source_id ||
    comment?.source_ids[0] ||
    comment?.document_id ||
    "";
  const source = w.sources.find((item) => item.id === effectiveSourceId);
  const document =
    w.documents.find(
      (item) => item.id === (source?.document_id || effectiveSourceId),
    ) ??
    w.documents.find((item) => item.name.toLowerCase().endsWith(".pdf")) ??
    w.documents[0];
  const target = w.sources.find(
    (item) => item.id === comment?.target_source_id,
  );
  const targetDocument = w.documents.find(
    (item) => item.id === target?.document_id,
  );
  const original = w.sources.find((item) => item.id === comment?.source_ids[0]);
  const originalDocument = w.documents.find(
    (item) => item.id === (original?.document_id || comment?.document_id),
  );
  const needsReview = w.comments.filter((item) => !item.reviewed);
  const processing = w.documents.some((item) => item.state === "queued");
  const hasOrderWork =
    w.comments.length > 0 ||
    w.documents.length > 0 ||
    (w.coordination?.tasks.length ?? 0) > 0;
  const visible = w.comments.filter(
    (item) =>
      (queueFilter === "all" ||
        (queueFilter === "review" ? !item.reviewed : item.status === "open")) &&
      `${item.number} ${item.text} ${item.author}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  useEffect(() => {
    setEditing(selectedId === "new");
  }, [selectedId]);
  const select = (item: RecordComment) => {
    selectComment(item.id);
    openSource(item.target_source_id || item.source_ids[0] || item.document_id);
    setAgentOpen(false);
    setQueueOpen(false);
  };
  const originalActive =
    !!comment &&
    (effectiveSourceId === original?.id ||
      effectiveSourceId === comment.document_id);
  const targetActive = !!target && effectiveSourceId === target.id;
  return (
    <section className="rivet-workbench" aria-label="Drawing review workbench">
      <div className="workbench-review-bar">
        <div>
          <RivetIcon name="review" size={19} />
          <span>
            {needsReview.length
              ? `${needsReview.length} ${needsReview.length === 1 ? "comment needs" : "comments need"} review`
              : w.comments.length
                ? "Extracted comments reviewed"
                : processing
                  ? "Reading documents…"
                  : w.documents.length
                    ? "No extracted comments — review sources"
                    : "Add a document to begin"}
          </span>
        </div>
        {hasOrderWork && (
          <div>
            <button
              className="text-button"
              onClick={openReadiness}
              title="Team assignments, owners, and readiness"
            >
              Team tasks
              <span>
                {w.coordination?.tasks.filter((task) => task.status !== "done")
                  .length ?? 0}
              </span>
              <ArrowUpRight size={14} />
            </button>
            {w.comments.length > 0 && (
              <button
                className={
                  "secondary workbench-queue-toggle" +
                  (queueOpen ? " active" : "")
                }
                aria-expanded={queueOpen}
                aria-controls="workbench-review-queue"
                onClick={() => setQueueOpen(!queueOpen)}
              >
                <List size={17} />
                Review queue<span>{needsReview.length}</span>
              </button>
            )}
          </div>
        )}
      </div>
      {queueOpen && (
        <section
          className="workbench-queue"
          id="workbench-review-queue"
          aria-label="Review queue"
        >
          <div className="workbench-queue-toolbar">
            <label>
              <MagnifyingGlass size={15} />
              <input
                aria-label="Search review queue"
                placeholder="Find a comment…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <select
              aria-label="Review queue filter"
              value={queueFilter}
              onChange={(event) =>
                setQueueFilter(event.target.value as typeof queueFilter)
              }
            >
              <option value="review">Needs review</option>
              <option value="open">Open comments</option>
              <option value="all">All comments</option>
            </select>
            <button className="text-button" onClick={openComments}>
              Full comment log
              <ArrowUpRight size={14} />
            </button>
            <button
              className="icon-button"
              aria-label="Close review queue"
              onClick={() => setQueueOpen(false)}
            >
              <X size={17} />
            </button>
          </div>
          <div className="workbench-queue-rows">
            {visible.map((item) => (
              <button
                key={item.id}
                className={item.id === selectedId ? "is-selected" : ""}
                onClick={() => select(item)}
              >
                <span className="workbench-queue-ref">
                  {item.number}
                  <small>Rev {item.revision || "—"}</small>
                </span>
                <span>
                  <strong>{item.text}</strong>
                  <small>
                    {item.author || "Author not stated"} ·{" "}
                    {item.source_page
                      ? `Source p. ${item.source_page}`
                      : "Source attached"}
                  </small>
                </span>
                <span className="workbench-queue-state">
                  {item.reviewed ? item.status : "Needs review"}
                </span>
                <ArrowRight size={15} />
              </button>
            ))}
            {!visible.length && (
              <p className="workbench-queue-empty">
                No comments in this view.{" "}
                <button
                  className="text-button"
                  onClick={() => {
                    setQueueFilter("all");
                    setQuery("");
                  }}
                >
                  View all comments
                </button>
              </p>
            )}
          </div>
        </section>
      )}
      <div className="workbench-desk">
        <div className="workbench-document">
          <div className="workbench-document-title">
            <RivetIcon name="drawing" size={20} />
            <label>
              <span className="sr-only">Current source document</span>
              <select
                aria-label="Current source document"
                value={document?.id || ""}
                onChange={(event) => openSource(event.target.value)}
              >
                {!document && <option value="">No source document</option>}
                {w.documents.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                    {item.revision_label ? ` · Rev ${item.revision_label}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="icon-button"
              aria-label="Add source documents"
              onClick={addDocuments}
            >
              <Plus size={18} />
            </button>
          </div>
          {comment && (
            <div
              className="workbench-document-locations"
              aria-label="Source and drawing locations"
            >
              <button
                aria-pressed={originalActive}
                onClick={() => openSource(original?.id || comment.document_id)}
                disabled={!original && !comment.document_id}
              >
                <RivetIcon name="comment" size={15} />
                Source comment
                {comment.source_page ? ` · p. ${comment.source_page}` : ""}
              </button>
              <button
                aria-pressed={targetActive}
                onClick={() => target && openSource(target.id)}
                disabled={!target}
              >
                <RivetIcon name="drawing" size={15} />
                {target
                  ? `Drawing reference${target.location.page ? ` · p. ${target.location.page}` : ""}`
                  : "Drawing not linked"}
              </button>
            </div>
          )}
          {document ? (
            <OrderEvidence
              key={document.id}
              inline
              workbench
              document={document}
              sources={w.sources.filter(
                (item) => item.document_id === document.id,
              )}
              selectedId={
                source?.document_id === document.id ? source.id : undefined
              }
              selectionRequest={sourceRequest}
              close={() => {}}
              initialPosition={positions.current.get(document.id)}
              onPositionChange={(position) =>
                positions.current.set(document.id, position)
              }
            />
          ) : (
            <Empty
              icon={<RivetIcon name="drawing" size={38} />}
              title="The original drawing belongs here"
              action={
                <button className="primary" onClick={addDocuments}>
                  <Plus size={16} />
                  Add documents
                </button>
              }
            >
              Upload a submittal, returned markup, or email. Rivet keeps the
              actual source beside each record.
            </Empty>
          )}
          <div className="workbench-source-route">
            <RivetIcon name="evidence" size={15} />
            {comment ? (
              <>
                <strong>Comment {comment.number}</strong>
                <span>→</span>
                <span>
                  Source{" "}
                  {comment.source_page
                    ? `p. ${comment.source_page}`
                    : "attached"}
                </span>
                <span>→</span>
                <span>
                  {target
                    ? `Drawing ${target.location.page ? `p. ${target.location.page}` : "linked"}`
                    : "Drawing reference needs review"}
                </span>
              </>
            ) : (
              <span>Original source · document contents remain unchanged</span>
            )}
          </div>
        </div>
        <aside
          ref={inspector}
          className="workbench-inspector"
          aria-label="Context inspector"
        >
          <nav className="workbench-inspector-tabs" aria-label="Context views">
            <button
              aria-pressed={!agentOpen}
              onClick={() => setAgentOpen(false)}
            >
              <RivetIcon name="comment" size={16} />
              Record
            </button>
            <button aria-pressed={agentOpen} onClick={() => setAgentOpen(true)}>
              <RivetIcon name="agent" size={16} />
              Rivet work
              {agentWork.working ? (
                <span className="workbench-work-badge is-working">Working</span>
              ) : agentWork.ready > 0 ? (
                <span className="workbench-work-badge">
                  {agentWork.ready} ready
                </span>
              ) : agentWork.needsInput > 0 ? (
                <span className="workbench-work-badge">Needs input</span>
              ) : null}
            </button>
          </nav>
          {w.comments.length > 0 && (
            <nav
              className="workbench-comment-nav"
              aria-label="Comment navigation"
            >
              <button
                className="icon-button"
                aria-label="Previous comment"
                disabled={commentIndex <= 0}
                onClick={() => select(w.comments[commentIndex - 1])}
              >
                <ArrowLeft size={16} />
              </button>
              <select
                aria-label="Current comment"
                value={comment?.id || ""}
                onChange={(event) => {
                  const item = w.comments.find(
                    (value) => value.id === event.target.value,
                  );
                  if (item) select(item);
                }}
              >
                {!comment && (
                  <option value="">
                    {selectedId === "new" ? "New comment" : "Choose a comment"}
                  </option>
                )}
                {w.comments.map((item) => (
                  <option key={item.id} value={item.id}>
                    Comment {item.number || "—"}
                    {item.source_page ? ` · p. ${item.source_page}` : ""}
                    {item.revision ? ` · Rev ${item.revision}` : ""} ·{" "}
                    {item.text.slice(0, 80)}
                  </option>
                ))}
              </select>
              <button
                className="icon-button"
                aria-label="Next comment"
                disabled={commentIndex >= w.comments.length - 1}
                onClick={() => select(w.comments[commentIndex + 1])}
              >
                <ArrowRight size={16} />
              </button>
            </nav>
          )}
          <div className="workbench-record-context" hidden={agentOpen}>
            {editing ? (
              renderEditor(() => setEditing(false))
            ) : comment ? (
              <>
                <div className="workbench-comment-heading">
                  <span>Comment {comment.number}</span>
                  <span
                    className={
                      "workbench-review-status" +
                      (!comment.reviewed ? " needs-review" : "")
                    }
                  >
                    <RivetIcon name="review" size={14} />
                    {comment.reviewed ? "Reviewed" : "Needs review"}
                  </span>
                </div>
                <p className="workbench-comment-text">{comment.text}</p>
                <p
                  className="workbench-comment-author"
                  title={comment.authored_at}
                >
                  {comment.author || "Author not stated"}
                  {comment.authored_at
                    ? ` · ${readableDate(comment.authored_at)}`
                    : ""}
                </p>
                <CommentAttribution comment={comment} />
                <section className="workbench-context-section">
                  <h3>Source annotation</h3>
                  <button
                    className="workbench-evidence-reference"
                    onClick={() =>
                      openSource(original?.id || comment.document_id)
                    }
                    disabled={!originalDocument}
                  >
                    <RivetIcon name="evidence" size={18} />
                    <span>
                      {originalDocument?.name || "Original source unavailable"}
                      <small>
                        {comment.source_page
                          ? `Source page ${comment.source_page}`
                          : "Original text retained"}
                      </small>
                    </span>
                    <ArrowUpRight size={15} />
                  </button>
                </section>
                <section className="workbench-context-section">
                  <h3>Referenced drawing</h3>
                  {target ? (
                    <>
                      <button
                        className="workbench-evidence-reference"
                        onClick={() => openSource(target.id)}
                      >
                        <RivetIcon name="drawing" size={18} />
                        <span>
                          {targetDocument?.name || "Linked drawing"}
                          <small>
                            {target.location.page
                              ? `Drawing page ${target.location.page}`
                              : "Linked source area"}{" "}
                            ·{" "}
                            {comment.reviewed
                              ? "Human reviewed"
                              : "Needs human review"}
                          </small>
                        </span>
                        <ArrowUpRight size={15} />
                      </button>
                      <p>
                        The comment location and its drawing reference are
                        recorded separately.
                      </p>
                    </>
                  ) : (
                    <>
                      <p>
                        No confirmed drawing location is linked to this comment.
                      </p>
                      <button
                        className="text-button"
                        disabled={busy || agentWork.working}
                        onClick={() => {
                          setAgentOpen(true);
                          setTaskRequest({
                            id: crypto.randomUUID(),
                            goal: DRAWING_REFERENCE_GOAL,
                          });
                        }}
                      >
                        Prepare a reference check
                        <ArrowRight size={14} />
                      </button>
                    </>
                  )}
                </section>
                <section className="workbench-context-section">
                  <div className="workbench-section-title">
                    <h3>Manufacturer response</h3>
                    <span className={"record-status " + comment.status}>
                      {comment.status}
                    </span>
                  </div>
                  {comment.response ? (
                    <>
                      <p className="workbench-recorded-response">
                        {comment.response}
                      </p>
                      <small>
                        {comment.responder || "Response author not recorded"} ·
                        Recorded by team
                      </small>
                    </>
                  ) : (
                    <p>
                      No engineering response recorded. Your team reviews and
                      records the response.
                    </p>
                  )}
                </section>
                <button
                  className="primary workbench-edit-record"
                  onClick={() => setEditing(true)}
                >
                  Review record & response
                  <ArrowRight size={16} />
                </button>
                <div className="workbench-context-bottom">
                  <button className="text-button" onClick={() => openChanges()}>
                    Related changes
                    <ArrowUpRight size={13} />
                  </button>
                  {needsReview.some((item) => item.id !== comment.id) && (
                    <button
                      className="text-button"
                      onClick={() =>
                        select(
                          needsReview.find((item) => item.id !== comment.id)!,
                        )
                      }
                    >
                      Next needing review
                      <ArrowRight size={13} />
                    </button>
                  )}
                </div>
              </>
            ) : (
              <div className="workbench-context-empty">
                <RivetIcon name="comment" size={30} />
                <h2>
                  {w.comments.length
                    ? "Select a comment to review"
                    : processing
                      ? "Reading your documents"
                      : w.documents.length
                        ? "Review the source contents"
                        : "Build the record from its sources"}
                </h2>
                <p>
                  {w.comments.length
                    ? "Use the review queue to open a comment beside the original drawing."
                    : processing
                      ? "Extracted comments will appear here when processing finishes."
                      : w.documents.length
                        ? "No comments have been extracted. Review the original, or record a comment manually."
                        : "Add a returned document in the source area, then review its comments here."}
                </p>
                {w.comments.length > 0 && (
                  <button
                    className="secondary"
                    onClick={() => setQueueOpen(true)}
                  >
                    Open review queue
                    <ArrowRight size={15} />
                  </button>
                )}
                <button
                  className="text-button"
                  onClick={() => {
                    selectComment("new");
                    setEditing(true);
                  }}
                >
                  Add a comment manually
                  <Plus size={14} />
                </button>
              </div>
            )}
          </div>
          <RecordAgent
            w={w}
            open={agentOpen}
            setOpen={setAgentOpen}
            save={save}
            saveError={error}
            recordBusy={busy}
            focusedCommentId={comment?.id || ""}
            focusedSourceId={sourceId || document?.id || ""}
            onWorkStateChange={setAgentWork}
            taskRequest={taskRequest}
            composerRequest={composerRequest}
            onTaskRequestConsumed={(id) =>
              setTaskRequest((current) => (current?.id === id ? null : current))
            }
            openSource={openSource}
            openComment={(id) => {
              const item = w.comments.find((value) => value.id === id);
              if (item) select(item);
            }}
            openChanges={openChanges}
            openWork={openReadiness}
          />
        </aside>
      </div>
      <div className="workbench-command-bar">
        <RivetIcon name="agent" size={19} />
        <button
          onClick={() => {
            setAgentOpen(true);
            setComposerRequest((request) => request + 1);
          }}
        >
          Give Rivet a task or ask about this {comment ? "comment" : "order"}…
        </button>
        <span>
          Context: {comment ? `comment ${comment.number}` : "this order"}
        </span>
        <kbd>⌘ J</kbd>
      </div>
    </section>
  );
}

function readableDate(value: string) {
  const match =
    value.match(/^D:(\d{4})(\d{2})(\d{2})/) ||
    value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return value;
  const year = Number(match[1]),
    month = Number(match[2]),
    day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  )
    return value;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
