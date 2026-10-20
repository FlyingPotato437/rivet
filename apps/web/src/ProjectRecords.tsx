import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CaretDown,
  FileText,
  MagnifyingGlass,
  SlidersHorizontal,
  X,
} from "@phosphor-icons/react";
import { api } from "./api";
import { useAccount } from "./Auth";
import { Busy, ErrorNote } from "./ui";
import { OrderEvidence } from "./OrderEvidence";
import { ReviewOutputs } from "./RevisionReview";
import { ConnectedSources } from "./ConnectedSources";
import { TranscriptionCorrection } from "./TranscriptionCorrection";
import { resolveRecordSource } from "./record-source-navigation";
import { responseNoteWrite, visibleProjectComments } from "./project-records";
import type { ProjectFlow } from "./project-flow";
import type { RecordComment, RecordView } from "./record-types";
import "./project-records.css";

const flags: Record<string, string> = {
  scope_change: "Possible scope change",
  late_change: "Change after approval",
  unresolved: "Earlier point still open",
  repeated: "Repeated comment",
};

/** One reading surface. Checking a comment never starts another review workflow. */
export function ProjectRecords({
  id,
  navigate: navigateTo,
  notify,
}: {
  id: string;
  navigate: (to: string) => void;
  notify: (message: string) => void;
}) {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const files = params.get("tab") === "documents" || !!params.get("source");
  const q = useQuery({
    queryKey: ["record", id],
    queryFn: () => api<RecordView>(`/orders/${id}/record`),
    refetchInterval: 5000,
  });
  const project = useQuery({
    queryKey: ["project-work", id],
    queryFn: () => api<ProjectFlow>(`/project-work/${id}`),
    refetchInterval: 10000,
  });
  const [search, setSearch] = useState("");
  const [revision, setRevision] = useState("latest");
  const [filtering, setFiltering] = useState(false);
  const [dirty, setDirty] = useState(false);
  function navigate(to: string) {
    if (!dirty || window.confirm("Discard your unsaved response note?"))
      navigateTo(to);
  }
  useEffect(() => {
    if (!dirty) return;
    const protect = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty]);
  const w = q.data;
  if (!w)
    return (
      <main className="page project-records">
        {q.isError ? (
          <ErrorNote message={(q.error as Error).message} />
        ) : (
          <Busy text="Opening order records…" />
        )}
      </main>
    );
  const comments = visibleProjectComments(w, search, revision);
  const requested = params.get("comment");
  const selected =
    comments.find((c) => c.id === requested) ||
    (requested ? w.comments.find((c) => c.id === requested) : comments[0]);
  const sourceId = params.get("source") || "";
  const link = sourceId ? resolveRecordSource(w, sourceId, requested) : null;
  const doc = link ? w.documents.find((d) => d.id === link.documentId) : null;
  const docs = w.documents.filter((d) =>
    `${d.email?.subject || ""} ${d.name} ${d.revision_label}`
      .toLowerCase()
      .includes(search.trim().toLowerCase()),
  );
  const revisions = [
    ...new Set(w.comments.map((c) => c.revision).filter(Boolean)),
  ];
  const go = (tab: string) => {
    setSearch("");
    navigate(`order/${id}?view=tools&tab=${tab}`);
  };
  return (
    <main className="page project-records">
      <button
        className="text-button project-back"
        onClick={() => navigate(`order/${id}`)}
      >
        <ArrowLeft size={16} /> Back to order
      </button>
      <header className="project-records-heading">
        <div>
          <span className="project-eyebrow">{w.order.number}</span>
          <h1>{files ? "Files" : "Comments"}</h1>
          <p>{project.data?.name || w.order.title}</p>
        </div>
        <details className="project-downloads">
          <summary>Downloads</summary>
          <ReviewOutputs id={id} number={w.order.number} />
        </details>
      </header>
      <nav className="project-records-nav" aria-label="Order records">
        <button
          aria-current={!files ? "page" : undefined}
          onClick={() => go("comments")}
        >
          Comments <span>{w.comments.length}</span>
        </button>
        <button
          aria-current={files ? "page" : undefined}
          onClick={() => go("documents")}
        >
          Files <span>{w.documents.length}</span>
        </button>
      </nav>
      {q.isError && (
        <ErrorNote message="Updates are delayed. Showing saved records." />
      )}
      <div className="project-records-toolbar">
        <label>
          <MagnifyingGlass size={17} />
          <input
            aria-label={files ? "Find a file" : "Find a comment"}
            placeholder={files ? "Find a file…" : "Find a comment…"}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        {!files && (
          <>
            <span>
              {revision === "latest"
                ? `Latest return${w.revision_review?.latest_revision ? ` · Rev ${w.revision_review.latest_revision}` : ""}`
                : revision === "all"
                  ? "All revisions"
                  : `Rev ${revision}`}
            </span>
            <button
              className="text-button"
              aria-expanded={filtering}
              onClick={() => setFiltering(!filtering)}
            >
              <SlidersHorizontal size={17} /> Filter
            </button>
          </>
        )}
      </div>
      {!files && filtering && (
        <div className="project-records-filters">
          <label>
            Revision{" "}
            <select
              aria-label="Revision"
              value={revision}
              onChange={(e) => setRevision(e.target.value)}
            >
              <option value="latest">Latest return</option>
              <option value="all">All revisions</option>
              {revisions.map((r) => (
                <option key={r} value={r}>
                  Rev {r}
                </option>
              ))}
            </select>
          </label>
          <button
            className="text-button"
            onClick={() => {
              setRevision("latest");
              setSearch("");
              setFiltering(false);
            }}
          >
            Reset
          </button>
        </div>
      )}
      <div className={`project-records-desk${files ? " files" : ""}`}>
        <section
          className="project-records-list"
          aria-label={files ? "Project files" : "Comment log"}
        >
          {files
            ? docs.map((d) => (
                <button
                  key={d.id}
                  className={doc?.id === d.id ? "selected" : ""}
                  aria-pressed={doc?.id === d.id}
                  onClick={() =>
                    navigate(
                      `order/${id}?view=tools&tab=documents&source=${encodeURIComponent(d.id)}`,
                    )
                  }
                >
                  <FileText size={19} />
                  <span>
                    <strong>{d.email?.subject || d.name}</strong>
                    <small>
                      {d.revision_label ? `Rev ${d.revision_label} · ` : ""}
                      {d.state === "ready"
                        ? "Source file"
                        : ["queued", "processing", "parsing"].includes(d.state)
                          ? "Rivet is reading this file"
                          : "Check file processing"}
                    </small>
                  </span>
                </button>
              ))
            : comments.map((c) => {
                const flag = w.revision_review?.flags.find(
                  (f) => f.comment_id === c.id && flags[f.kind],
                );
                return (
                  <button
                    key={c.id}
                    className={selected?.id === c.id ? "selected" : ""}
                    aria-pressed={selected?.id === c.id}
                    aria-label={`Read comment ${c.log_reference || c.number}`}
                    onClick={() =>
                      navigate(
                        `order/${id}?view=tools&tab=comments&comment=${c.id}`,
                      )
                    }
                  >
                    <span className="project-comment-id">
                      {c.log_reference || c.number}
                    </span>
                    <span>
                      <strong>{c.text}</strong>
                      <small>
                        {c.status === "closed"
                          ? "Resolved"
                          : c.response
                            ? "Response recorded"
                            : "Captured from source"}
                        {flag ? ` · ${flags[flag.kind]}` : ""}
                      </small>
                    </span>
                  </button>
                );
              })}
          {!(files ? docs.length : comments.length) && (
            <div className="project-records-empty">
              <h3>
                {search
                  ? "No matches"
                  : files
                    ? "No files yet"
                    : "No comments in this view"}
              </h3>
              <p>
                {search
                  ? "Try another word or clear the search."
                  : files
                    ? "Files captured from Outlook or uploaded to this order appear here."
                    : "Rivet keeps returned comments here as it reads the drawings."}
              </p>
              {!files && w.comments.length > 0 && (
                <button
                  className="text-button"
                  onClick={() => {
                    setSearch("");
                    setRevision("all");
                  }}
                >
                  Show all comments
                </button>
              )}
            </div>
          )}
        </section>
        <section
          className="project-records-detail"
          aria-label={files ? "File preview" : "Selected comment"}
        >
          {files ? (
            doc ? (
              <>
                <header>
                  <FileText size={18} />
                  <strong>{doc.email?.subject || doc.name}</strong>
                  <button
                    className="icon-button"
                    aria-label="Close file preview"
                    onClick={() => go("documents")}
                  >
                    <X size={17} />
                  </button>
                </header>
                <OrderEvidence
                  key={doc.id}
                  document={doc}
                  sources={w.sources.filter((s) => s.document_id === doc.id)}
                  selectedId={sourceId}
                  inline
                  workbench
                  close={() => go("documents")}
                />
              </>
            ) : (
              <div className="project-records-empty">
                <FileText size={28} />
                <h3>{sourceId ? "Source unavailable" : "Choose a file"}</h3>
                <p>
                  {sourceId
                    ? "This source is not available in this order. No substitute was opened."
                    : "The original and its extracted text open here."}
                </p>
              </div>
            )
          ) : selected ? (
            <CommentReading
              key={selected.id}
              comment={selected}
              w={w}
              notify={notify}
              navigate={navigate}
              setDirty={setDirty}
            />
          ) : (
            <div className="project-records-empty">
              <h3>Select a comment</h3>
              <p>Its source and recorded response will appear here.</p>
            </div>
          )}
        </section>
      </div>
      <footer className="project-records-footer">
        <span>
          {files ? docs.length : comments.length} {files ? "files" : "comments"}
          {!files && !!w.email_context?.length
            ? " · email cover notes stay with the source files"
            : ""}
        </span>
        <details>
          <summary>More tools</summary>
          <button
            className="text-button"
            onClick={() => navigate(`order/${id}?view=advanced&tab=comments`)}
          >
            Edit records and assignments
          </button>
        </details>
      </footer>
      {files && (
        <details className="project-connected-details">
          <summary>Connected records</summary>
          <ConnectedSources orderId={id} />
        </details>
      )}
    </main>
  );
}

function CommentReading({
  comment: c,
  w,
  notify,
  navigate,
  setDirty,
}: {
  comment: RecordComment;
  w: RecordView;
  notify: (message: string) => void;
  navigate: (to: string) => void;
  setDirty: (value: boolean) => void;
}) {
  const qc = useQueryClient(),
    account = useAccount();
  const [edit, setEdit] = useState<{
    original: RecordComment;
    version: number;
    text: string;
  } | null>(null);
  const [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const [correctingTranscription, setCorrectingTranscription] = useState(false);
  useEffect(() => {
    setDirty(!!edit || correctingTranscription);
    return () => setDirty(false);
  }, [!!edit, correctingTranscription, setDirty]);
  const source = w.sources.find((s) => c.source_ids.includes(s.id));
  const doc = w.documents.find(
    (d) => d.id === (source?.document_id || c.document_id),
  );
  const commentFlags =
    w.revision_review?.flags.filter(
      (f) => f.comment_id === c.id && flags[f.kind],
    ) || [];
  async function save() {
    if (!edit) return;
    setSaving(true);
    setError("");
    try {
      const result = await api<RecordView>(
        `/orders/${w.order.id}/record/comments/${c.id}`,
        responseNoteWrite(edit.original, edit.text, edit.version, account.name),
      );
      qc.setQueryData(["record", w.order.id], result);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["project-work"] }),
        qc.invalidateQueries({
          queryKey: ["order-reply-preparation", w.order.id],
        }),
      ]);
      setEdit(null);
      notify("Response note saved. Outlook controls sending.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <header>
        <strong>{c.log_reference || `Comment ${c.number}`}</strong>
        <span>
          Rev {c.revision || "—"}
          {c.source_page ? ` · Page ${c.source_page}` : ""}
        </span>
      </header>
      <div className="project-comment-reading">
        <span className="project-reading-label">Customer comment</span>
        <h2>{c.text}</h2>
        {["org:admin", "org:member"].includes(account.role) && (
          <TranscriptionCorrection
            key={`${w.order.id}:${c.id}`}
            w={w}
            comment={c}
            onEditingChange={setCorrectingTranscription}
          />
        )}
        {commentFlags.map((f) => (
          <p key={f.id} className="project-comment-finding">
            <strong>{flags[f.kind]}</strong>
            {f.detail}
          </p>
        ))}
        <div className="project-response-heading">
          <h3>Recorded response</h3>
          {["org:admin", "org:member"].includes(account.role) && !edit && (
            <button
              className="text-button"
              onClick={() =>
                setEdit({
                  original: structuredClone(c),
                  version: w.version,
                  text: c.response,
                })
              }
            >
              {c.response ? "Edit note" : "Add note"}
            </button>
          )}
        </div>
        {edit ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <label className="project-response-label">
              Response note
              <textarea
                aria-label="Response note"
                value={edit.text}
                maxLength={16000}
                rows={6}
                onChange={(e) => setEdit({ ...edit, text: e.target.value })}
              />
            </label>
            <p className="project-records-note">
              Saves to this order. It does not send a reply or approve a
              change.
            </p>
            {error && <ErrorNote message={error} />}
            <div className="project-response-actions">
              <button className="primary" disabled={saving}>
                {saving ? "Saving…" : "Save note"}
              </button>
              <button
                type="button"
                className="text-button"
                disabled={saving}
                onClick={() => {
                  setEdit(null);
                  setError("");
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        ) : c.response ? (
          <p className="project-response-text">{c.response}</p>
        ) : (
          <div className="project-records-note">
            <p>No separate response note is recorded for this comment.</p>
            <button
              className="text-button"
              onClick={() => navigate(`order/${w.order.id}`)}
            >
              Read Rivet’s prepared reply
            </button>
          </div>
        )}
        {doc && (
          <details className="project-comment-source">
            <summary>
              <FileText size={17} />
              <span>
                View source
                <small>
                  {doc.email?.subject || doc.name}
                  {c.source_page ? ` · p. ${c.source_page}` : ""}
                </small>
              </span>
              <CaretDown size={15} />
            </summary>
            {source?.text && <blockquote>{source.text}</blockquote>}
            <OrderEvidence
              document={doc}
              sources={w.sources.filter((s) => s.document_id === doc.id)}
              selectedId={source?.id}
              inline
              workbench
              close={() => {}}
            />
          </details>
        )}
        <details className="project-comment-provenance">
          <summary>Source details</summary>
          <p>
            Original comment {c.number} · {c.author || "Author not recorded"}
          </p>
          <p>
            {c.reviewed
              ? "Reviewed record"
              : "Imported record · not marked as human reviewed"}
          </p>
          <button
            className="text-button"
            onClick={() =>
              navigate(
                `order/${w.order.id}?view=advanced&tab=work&comment=${c.id}&edit=1`,
              )
            }
          >
            Edit original record
          </button>
        </details>
      </div>
    </>
  );
}
