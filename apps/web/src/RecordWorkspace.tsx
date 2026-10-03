import { useAccount } from "./Auth";
import { OrderInboxPanel, SendNoticeButton } from "./Integrations";
import { useState, useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  ChatText,
  Check,
  CheckCircle,
  Clock,
  DownloadSimple,
  FileText,
  LinkSimple,
  MagnifyingGlass,
  Plus,
  UploadSimple,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { Busy, Empty, ErrorNote, Mark, Modal } from "./ui";
import { OrderEvidence } from "./OrderEvidence";
import { CommentConnections, RecordContext } from "./RecordConnections";
import type { RecordView, RecordComment, RecordChange } from "./record-types";
import "./order-workspace.css";
import "./record-workspace.css";
const tabs = ["comments", "changes", "documents", "sharing"] as const;
type Tab = (typeof tabs)[number];
const names = {
  comments: "Comment log",
  changes: "Changes & history",
  documents: "Documents",
  sharing: "Approved record",
};
const values = (form: HTMLFormElement) =>
  Object.fromEntries(new FormData(form));
const draftComment: Partial<RecordComment> = {
  number: "",
  revision: "",
  text: "",
  author: "",
  authored_at: "",
  status: "open",
  response: "",
  responder: "",
  target_source_id: "",
  linked_email_id: "",
  reviewed: false,
};
export function RecordWorkspace({
  id,
  navigate,
  notify,
}: {
  id: string;
  navigate: (to: string) => void;
  notify: (s: string) => void;
}) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["record", id],
    queryFn: () => api<RecordView>(`/orders/${id}/record`),
    refetchInterval: 4000,
  });
  const w = query.data;
  const incoming = new URLSearchParams(location.hash.split("?")[1] ?? "").get(
    "tab",
  );
  const tab: Tab = tabs.includes(incoming as Tab)
    ? (incoming as Tab)
    : incoming === "history"
      ? "changes"
      : "comments";
  const [selected, setSelected] = useState<string | null>(null),
    [source, setSource] = useState(""),
    [upload, setUpload] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [filter, setFilter] = useState("all"),
    [search, setSearch] = useState(""),
    [revision, setRevision] = useState("all");
  const linkedComment = new URLSearchParams(
    location.hash.split("?")[1] ?? "",
  ).get("comment");
  useEffect(() => {
    if (tab === "comments" && linkedComment) {
      setSelected(linkedComment);
      setFilter("all");
      setSearch("");
      setRevision("all");
    }
  }, [tab, linkedComment]);
  async function save(path: string, body: unknown) {
    setBusy(true);
    setError("");
    try {
      const result = await api<RecordView>(`/orders/${id}/record${path}`, body);
      qc.setQueryData(["record", id], result);
      void qc.invalidateQueries({ queryKey: ["records"] });
      notify("Record saved");
      return true;
    } catch (e) {
      setError((e as Error).message);
      void query.refetch();
      return false;
    } finally {
      setBusy(false);
    }
  }
  if (!w)
    return (
      <main className="page">
        {query.isError ? (
          <ErrorNote message={(query.error as Error).message} />
        ) : (
          <Busy text="Loading comment record…" />
        )}
      </main>
    );
  const comment =
    selected === "new"
      ? draftComment
      : w.comments.find((c) => c.id === selected);
  const selectedSource = w.sources.find((s) => s.id === source);
  const document = w.documents.find(
    (d) => d.id === (selectedSource?.document_id || source),
  );
  const visible = w.comments
    .filter(
      (c) =>
        (filter === "all" ||
          (filter === "review" && !c.reviewed) ||
          c.status === filter) &&
        (revision === "all" || c.revision === revision) &&
        `${c.text} ${c.number} ${c.author} ${c.response}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    )
    .sort(
      (a, b) =>
        w.documents.findIndex((d) => d.id === a.document_id) -
          w.documents.findIndex((d) => d.id === b.document_id) ||
        a.number.localeCompare(b.number, undefined, { numeric: true }),
    );
  const counts = {
    open: w.comments.filter((c) => c.status === "open").length,
    responded: w.comments.filter((c) => c.status === "responded").length,
    closed: w.comments.filter((c) => c.status === "closed").length,
    review: w.comments.filter((c) => !c.reviewed).length,
  };
  return (
    <main className="page record-workspace">
      <div className="record-breadcrumb">
        <button className="text-button" onClick={() => navigate("orders")}>
          <ArrowLeft size={14} />
          Orders
        </button>
        <span>/</span>
        <span>{w.order.number}</span>
        {w.order.synthetic && <small>Sample order</small>}
      </div>
      <header className="record-heading">
        <div>
          <h1>{w.order.title}</h1>
          <p>
            {w.order.customer}
            <span>·</span>
            {w.order.category}
          </p>
        </div>
        <button className="primary" onClick={() => setUpload(true)}>
          <UploadSimple size={17} />
          Add documents
        </button>
      </header>
      <div className="record-statusline">
        <span>
          <ChatText size={16} />
          {w.comments.length} comments
        </span>
        <button
          onClick={() => {
            setFilter("open");
            navigate(`order/${id}`);
          }}
        >
          {counts.open} open
        </button>
        <button
          onClick={() => {
            setFilter("review");
            navigate(`order/${id}`);
          }}
          className={counts.review ? "attention" : ""}
        >
          {counts.review > 0 && <WarningCircle size={15} />} {counts.review}{" "}
          need review
        </button>
        <span className="record-updated">Saved record · v{w.version}</span>
      </div>
      {demoNotice(w.order.synthetic)}
      <RecordContext
        w={w}
        openSharing={() => navigate(`order/${id}?tab=sharing`)}
        openChanges={() => navigate(`order/${id}?tab=changes`)}
      />
      <nav className="ow-tabs record-tabs" aria-label="Order record views">
        {tabs.map((t) => (
          <button
            key={t}
            className={tab === t ? "active" : ""}
            aria-current={tab === t ? "page" : undefined}
            onClick={() => {
              setSelected(null);
              navigate(`order/${id}?tab=${t}`);
            }}
          >
            {names[t]}
          </button>
        ))}
      </nav>
      {error && <ErrorNote message={error} />}{" "}
      {query.isError && (
        <ErrorNote message="Could not refresh. Showing the last saved record." />
      )}
      {tab === "comments" && (
        <>
          <div className="record-toolbar">
            <div className="record-filters">
              {[
                ["all", "All"],
                ["review", "Needs review"],
                ["open", "Open"],
                ["responded", "Responded"],
                ["closed", "Closed"],
              ].map(([value, label]) => (
                <button
                  key={value}
                  className={filter === value ? "selected" : ""}
                  aria-pressed={filter === value}
                  onClick={() => setFilter(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="record-tools">
              <label className="record-search">
                <MagnifyingGlass size={15} />
                <input
                  aria-label="Search comments"
                  placeholder="Search comments…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </label>
              <select
                aria-label="Filter revision"
                value={revision}
                onChange={(e) => setRevision(e.target.value)}
              >
                <option value="all">All revisions</option>
                {[...new Set(w.comments.map((c) => c.revision))].map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
              <button className="secondary" onClick={() => setSelected("new")}>
                <Plus size={15} />
                Add comment
              </button>
              {counts.review > 0 && (
                <button
                  className="primary"
                  onClick={() => {
                    const next = visible.find((c) => !c.reviewed);
                    if (next) setSelected(next.id);
                    else {
                      setFilter("review");
                      setSearch("");
                      setRevision("all");
                      setSelected(w.comments.find((c) => !c.reviewed)!.id);
                    }
                  }}
                >
                  Review next <ArrowRight size={14} />
                </button>
              )}
            </div>
          </div>
          <div className={"record-log-layout " + (comment ? "has-detail" : "")}>
            <section className="record-log" aria-label="Comment log">
              <div className="record-log-head">
                <span>Comment / revision</span>
                <span>Comment and source</span>
                <span>Status</span>
              </div>
              {visible.map((c) => (
                <button
                  key={c.id}
                  className={
                    "record-comment-row " +
                    (selected === c.id ? "selected" : "")
                  }
                  onClick={() => setSelected(c.id)}
                >
                  <span className="record-comment-number">
                    {c.number}
                    <small>Rev {c.revision}</small>
                  </span>
                  <span className="record-comment-summary">
                    <strong>{c.text}</strong>
                    <span>
                      {c.author || "Author not stated"}
                      <i>·</i>
                      {c.source_page
                        ? `Source p. ${c.source_page}`
                        : c.origin === "manual"
                          ? "Manual entry"
                          : "Source attached"}
                      {c.target_source_id && (
                        <>
                          <i>·</i>Drawing linked
                        </>
                      )}
                    </span>
                    <small
                      className={!c.reviewed ? "attention" : "record-reviewed"}
                    >
                      {!c.reviewed ? (
                        <WarningCircle size={12} />
                      ) : (
                        <Check size={12} />
                      )}{" "}
                      {c.confidence}
                    </small>
                  </span>
                  <span className={"record-status " + c.status}>
                    {c.status}
                  </span>
                </button>
              ))}
              {!visible.length && (
                <Empty
                  title={
                    w.comments.length
                      ? "No matching comments"
                      : "No comments yet"
                  }
                  icon={<ChatText size={28} />}
                >
                  {w.documents.some((d) => d.state === "queued")
                    ? "Your files are being read. Comments will appear here."
                    : "Upload a marked-up PDF or EML email, or add a comment manually."}
                </Empty>
              )}
            </section>
            {comment && (
              <CommentEditor
                key={selected}
                comment={comment}
                w={w}
                busy={busy}
                hasNext={visible.some((c) => !c.reviewed && c.id !== selected)}
                close={() => setSelected(null)}
                openSource={setSource}
                openChanges={() =>
                  navigate(`order/${id}?tab=changes&comment=${selected}`)
                }
                save={async (body, advance) => {
                  if (
                    await save(
                      `/comments${selected === "new" ? "" : "/" + selected}`,
                      body,
                    )
                  )
                    setSelected(
                      advance
                        ? (visible.find((c) => !c.reviewed && c.id !== selected)
                            ?.id ?? null)
                        : null,
                    );
                }}
              />
            )}
          </div>
          <footer className="record-log-footer">
            <span>
              {visible.length} comments · Original source text is retained.
            </span>
            <Exports id={id} />
          </footer>
        </>
      )}
      {tab === "documents" && (
        <section>
          <div className="ow-section-heading">
            <div>
              <h2>Source documents</h2>
              <p>Original files, review markups, and imported emails.</p>
            </div>
            <button className="text-button" onClick={() => setUpload(true)}>
              <Plus size={16} />
              Add documents
            </button>
          </div>
          <div className="record-document-list">
            {w.documents.map((d) => (
              <button key={d.id} onClick={() => setSource(d.id)}>
                <FileText size={23} />
                <span>
                  <strong>{d.name}</strong>
                  <small>
                    {d.email
                      ? `${d.email.from} · ${d.email.subject}`
                      : d.role.replaceAll("_", " ")}
                    {d.revision_label ? ` · Rev ${d.revision_label}` : ""}
                  </small>
                </span>
                <span
                  className={
                    "record-status " + (d.state === "failed" ? "open" : "")
                  }
                >
                  {d.state === "queued"
                    ? "Processing"
                    : d.state === "failed"
                      ? "Failed"
                      : "View source"}
                </span>
                <ArrowUpRight size={16} />
              </button>
            ))}
          </div>
          {!w.documents.length && (
            <Empty title="No documents uploaded">
              Add the submittal and review comments to start the record.
            </Empty>
          )}
          <OrderInboxPanel id={w.order.id} />
        </section>
      )}
      {tab === "changes" && (
        <Changes
          w={w}
          error={error}
          busy={busy}
          save={save}
          openSource={setSource}
          openComment={(commentId) =>
            navigate(`order/${id}?tab=comments&comment=${commentId}`)
          }
          clearComment={() => navigate(`order/${id}?tab=changes`)}
          linkedComment={linkedComment}
        />
      )}
      {tab === "sharing" && (
        <Sharing
          w={w}
          saveError={error}
          busy={busy}
          save={save}
          notify={notify}
          refresh={() => void query.refetch()}
        />
      )}
      <RecordAssistant
        key={id}
        w={w}
        openSource={setSource}
        openComment={(id) => {
          setSelected(id);
          navigate(`order/${w.order.id}`);
        }}
        openChanges={() => navigate(`order/${w.order.id}?tab=changes`)}
      />
      {upload && (
        <RecordUpload
          w={w}
          close={() => setUpload(false)}
          done={() => {
            setUpload(false);
            void query.refetch();
            void qc.invalidateQueries({ queryKey: ["records"] });
            notify("Documents queued for reading");
          }}
        />
      )}
      {document && (
        <OrderEvidence
          key={source}
          document={document}
          sources={w.sources.filter((s) => s.document_id === document.id)}
          selectedId={selectedSource?.id}
          close={() => setSource("")}
        />
      )}
    </main>
  );
}
function Exports({ id }: { id: string }) {
  return (
    <div className="record-exports">
      <a href={`/api/orders/${id}/record/export?format=xlsx`}>
        <DownloadSimple size={14} />
        Excel log
      </a>
      <a href={`/api/orders/${id}/record/export?format=pdf`}>
        <DownloadSimple size={14} />
        PDF matrix
      </a>
    </div>
  );
}
function demoNotice(sample: boolean) {
  // Kept independent of account hooks so the loaded record keeps hook order stable.
  return sample ? (
    <p className="record-practice-note">
      Practice order · Responses and approvals entered here are test records.
      Public sources remain unchanged.
    </p>
  ) : null;
}
function AuditFields({ defaultReason = "" }: { defaultReason?: string }) {
  const account = useAccount();
  return (
    <>
      <div className="form-row">
        <label>
          Your name
          <input
            name="actor"
            required
            minLength={2}
            maxLength={100}
            placeholder="Name for the audit record"
            defaultValue={account.mode === "clerk" ? account.name : undefined}
            readOnly={account.mode === "clerk"}
          />
        </label>
        <label>
          Reason
          <input
            name="reason"
            required
            minLength={3}
            maxLength={2000}
            placeholder="What you reviewed or changed"
            defaultValue={defaultReason}
          />
        </label>
      </div>
    </>
  );
}
function CommentEditor({
  comment: c,
  w,
  busy,
  close,
  openSource,
  save,
  hasNext,
  openChanges,
}: {
  comment: Partial<RecordComment>;
  w: RecordView;
  busy: boolean;
  close: () => void;
  openSource: (s: string) => void;
  save: (b: unknown, advance?: boolean) => Promise<void>;
  hasNext: boolean;
  openChanges: () => void;
}) {
  const [editVersion] = useState(w.version);
  const [locationSearch, setLocationSearch] = useState("");
  const [targetSource, setTargetSource] = useState(c.target_source_id || "");
  const detail = useRef<HTMLElement>(null);
  useEffect(() => {
    if (window.matchMedia("(max-width: 1100px)").matches)
      detail.current?.scrollIntoView({ block: "start" });
  }, [c.id]);
  const matchingSources = w.sources.filter(
    (s) =>
      !w.documents.find((d) => d.id === s.document_id)?.email &&
      `${w.documents.find((d) => d.id === s.document_id)?.name} page ${s.location.page ?? ""} ${s.text}`
        .toLowerCase()
        .includes(locationSearch.toLowerCase()),
  );
  const sourceOptions = [
    ...new Map(
      [
        ...w.sources.filter((s) => s.id === targetSource),
        ...matchingSources.slice(0, 80),
      ].map((s) => [s.id, s]),
    ).values(),
  ];
  return (
    <aside className="record-detail" ref={detail}>
      <div className="record-detail-top">
        <div>
          <span className="eyebrow">
            {c.id ? "Comment details" : "Manual entry"}
          </span>
          <h2>{c.id ? `Comment ${c.number}` : "Add comment"}</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close comment"
          onClick={close}
        >
          <X size={18} />
        </button>
      </div>
      {c.flags && !c.reviewed && (
        <div className="record-unclear">
          <WarningCircle size={17} />
          <span>
            <strong>Needs human review</strong>
            {c.flags.map((f) => (
              <span key={f}>{f}</span>
            ))}
          </span>
        </div>
      )}
      {c.id && (
        <CommentConnections
          id={c.id}
          w={w}
          openSource={openSource}
          openChanges={openChanges}
        />
      )}
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const data = values(e.currentTarget);
          const advance =
            (e.nativeEvent as SubmitEvent).submitter?.getAttribute("value") ===
            "next";
          await save(
            {
              ...data,
              reviewed: data.reviewed === "on",
              expected_version: editVersion,
            },
            advance,
          );
        }}
      >
        {c.id && (
          <div className="record-metadata-summary">
            <span>
              <small>Author</small>
              {c.author || "Not stated"}
            </span>
            <span>
              <small>Comment date</small>
              {c.authored_at?.replace(
                /^D:(\d{4})(\d{2})(\d{2}).*/,
                "$1-$2-$3",
              ) || "Not stated"}
            </span>
            <span>
              <small>Revision</small>
              {c.revision || "Not stated"}
            </span>
          </div>
        )}
        <label>
          Comment
          <textarea
            name="text"
            required
            rows={4}
            maxLength={16000}
            defaultValue={c.text}
          />
        </label>
        {c.original_text && (
          <details className="record-original">
            <summary>Original comment · {c.origin}</summary>
            <blockquote>{c.original_text}</blockquote>
            {c.source_ids?.map((s) => (
              <button
                type="button"
                className="text-button"
                key={s}
                onClick={() => openSource(s)}
              >
                <ArrowUpRight size={14} />
                Open source{c.source_page ? ` · p. ${c.source_page}` : ""}
              </button>
            ))}
          </details>
        )}
        <details className="record-edit-metadata" open={!c.id || undefined}>
          <summary>Edit author, date, revision & drawing link</summary>
          <div className="record-edit-metadata-fields">
            <div className="form-row">
              <label>
                Comment number
                <input
                  name="number"
                  required
                  defaultValue={c.number}
                  maxLength={40}
                />
              </label>
              <label>
                Revision
                <input
                  name="revision"
                  defaultValue={c.revision}
                  maxLength={40}
                  placeholder="e.g. 02"
                />
              </label>
            </div>
            <div className="form-row">
              <label>
                Comment author
                <input
                  name="author"
                  defaultValue={c.author}
                  maxLength={180}
                  placeholder="Not stated"
                />
              </label>
              <label>
                Comment date
                <input
                  name="authored_at"
                  defaultValue={c.authored_at}
                  maxLength={80}
                  placeholder="As stated in source"
                />
              </label>
            </div>
            <label>
              Find a drawing area
              <input
                placeholder="File, page number, or text…"
                value={locationSearch}
                onChange={(e) => setLocationSearch(e.target.value)}
              />
            </label>
            {c.source_ids?.[0] && (
              <button
                type="button"
                className="text-button record-use-location"
                onClick={() => setTargetSource(c.source_ids![0])}
              >
                <LinkSimple size={14} />
                Use the original markup location
              </button>
            )}
            {c.source_ids?.[0] && (
              <small className="record-location-hint">
                Use only when the comment refers to the area marked in the
                source.
              </small>
            )}
            <label>
              Drawing location
              <select
                name="target_source_id"
                value={targetSource}
                onChange={(e) => setTargetSource(e.target.value)}
              >
                <option value="">Needs a location / not applicable</option>
                {sourceOptions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {w.documents.find((d) => d.id === s.document_id)?.name} ·{" "}
                    {s.location.page ? `p. ${s.location.page}` : "excerpt"} ·{" "}
                    {s.text.slice(0, 90)}
                  </option>
                ))}
              </select>
            </label>
            {targetSource && (
              <button
                type="button"
                className="text-button"
                onClick={() => openSource(targetSource)}
              >
                <LinkSimple size={14} />
                Open linked drawing area
              </button>
            )}
            <label>
              Linked email
              <select name="linked_email_id" defaultValue={c.linked_email_id}>
                <option value="">None</option>
                {w.documents
                  .filter((d) => d.email)
                  .map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.email?.subject || d.name}
                    </option>
                  ))}
              </select>
            </label>
          </div>
        </details>
        <label>
          Manufacturer response
          <textarea
            name="response"
            rows={3}
            maxLength={16000}
            defaultValue={c.response}
            placeholder="Record the team's response"
          />
        </label>
        <div className="form-row">
          <label>
            Response author
            <input
              name="responder"
              defaultValue={c.responder}
              maxLength={180}
            />
          </label>
          <label>
            Status
            <select name="status" defaultValue={c.status}>
              <option value="open">Open</option>
              <option value="responded">Responded</option>
              <option value="closed">Closed</option>
            </select>
          </label>
        </div>
        <label className="record-checkbox">
          <input type="checkbox" name="reviewed" defaultChecked={c.reviewed} />I
          reviewed the extracted fields and drawing location.
        </label>
        <AuditFields defaultReason="Reviewed source and comment details" />
        <div className="record-save-actions">
          <button className={hasNext ? "secondary" : "primary"} disabled={busy}>
            {busy ? (
              <Busy />
            ) : (
              <>
                <Check size={16} />
                Save comment
              </>
            )}
          </button>
          {hasNext && (
            <button
              className="primary"
              type="submit"
              value="next"
              disabled={busy}
            >
              Save & next <ArrowRight size={15} />
            </button>
          )}
        </div>
      </form>
    </aside>
  );
}
function RecordUpload({
  w,
  close,
  done,
}: {
  w: RecordView;
  close: () => void;
  done: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal title="Add documents" eyebrow="Order intake" onClose={close}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          const data = new FormData(e.currentTarget);
          try {
            for (const file of data.getAll("files") as File[]) {
              const body = new FormData();
              body.set("file", file);
              body.set(
                "revision_label",
                String(data.get("revision_label") || ""),
              );
              body.set("kind", String(data.get("kind")));
              if (file.name.toLowerCase().endsWith(".eml")) {
                body.delete("kind");
                await api(`/orders/${w.order.id}/record/email`, body);
              } else
                await api(`/projects/${w.order.project_id}/documents`, body);
            }
            done();
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="modal-intro">
          Add the submittal, marked-up files, or saved emails. Comments retain
          their original source.
        </p>
        <p className="record-note">
          Up to 20 MB per file and 150 pages per PDF. Split larger packages into
          named sections.
        </p>
        <label>
          Files
          <input
            type="file"
            name="files"
            required
            multiple
            accept=".pdf,.txt,.csv,.xlsx,.eml"
          />
        </label>
        <div className="form-row">
          <label>
            Document type
            <select name="kind" defaultValue="markups">
              <option value="markups">Review comments / markups</option>
              <option value="approval_drawing">Submittal / drawing</option>
              <option value="specification">Specification</option>
              <option value="auto">Identify from document</option>
            </select>
          </label>
          <label>
            Revision
            <input name="revision_label" maxLength={40} placeholder="e.g. 02" />
          </label>
        </div>
        <p className="record-note">
          PDF, TXT, CSV, XLSX, or EML · 20 MB per file. Email attachments are
          imported with the message. Scanned pages and unclear locations require
          review.
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
          <button className="primary" disabled={busy}>
            {busy ? <Busy text="Importing…" /> : "Import documents"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
function Changes({
  w,
  busy,
  save,
  openSource,
  error,
  openComment,
  clearComment,
  linkedComment,
}: {
  w: RecordView;
  error: string;
  busy: boolean;
  save: (p: string, b: unknown) => Promise<boolean>;
  openSource: (s: string) => void;
  openComment: (s: string) => void;
  clearComment: () => void;
  linkedComment: string | null;
}) {
  const [edit, setEdit] = useState<Partial<RecordChange> | null>(null),
    [before, setBefore] = useState(""),
    [after, setAfter] = useState("");
  const [editVersion, setEditVersion] = useState(w.version);
  const compared = useQuery({
    queryKey: ["record-compare", w.order.id, before, after],
    queryFn: () =>
      api<{
        changes: {
          kind: string;
          before: string;
          after: string;
          before_source_ids: string[];
          after_source_ids: string[];
        }[];
        note: string;
      }>(
        `/orders/${w.order.id}/record/compare?before=${before}&after=${after}`,
      ),
    enabled: !!before && !!after && before !== after,
  });
  return (
    <section>
      <div className="ow-section-heading">
        <div>
          <h2>Change record</h2>
          <p>What changed, who requested it, and the recorded approval.</p>
        </div>
        <button
          className="secondary"
          onClick={() => {
            setEditVersion(w.version);
            setEdit({ comment_ids: linkedComment ? [linkedComment] : [] });
          }}
        >
          <Plus size={16} />
          Record a change
        </button>
      </div>
      {linkedComment && (
        <div className="record-related-filter">
          Changes linked to comment{" "}
          {w.comments.find((c) => c.id === linkedComment)?.number || "—"}
          <button className="text-button" onClick={clearComment}>
            Show all changes <X size={13} />
          </button>
        </div>
      )}
      <div className="record-changes">
        {w.changes
          .filter(
            (c) => !linkedComment || c.comment_ids.includes(linkedComment),
          )
          .map((c) => (
            <article key={c.id}>
              <div>
                <span className="eyebrow">
                  Rev {c.from_revision || "—"} → {c.to_revision || "—"}
                </span>
                <h3>{c.title}</h3>
              </div>
              <div className="record-change-values">
                <span>
                  <small>Before</small>
                  {c.before || "Not stated"}
                </span>
                <ArrowRight size={17} />
                <span>
                  <small>After</small>
                  {c.after}
                </span>
              </div>
              <p>
                Requested by {c.requested_by || "not recorded"} ·{" "}
                {c.approved_by
                  ? `Approval recorded: ${c.approved_by} · ${c.approved_at}`
                  : "Approval not recorded"}
              </p>
              <div className="record-change-links">
                {c.comment_ids.map((id) => (
                  <button
                    className="text-button"
                    key={id}
                    onClick={() => openComment(id)}
                  >
                    Comment {w.comments.find((x) => x.id === id)?.number}
                    <ArrowUpRight size={12} />
                  </button>
                ))}
                <button
                  className="text-button"
                  onClick={() => {
                    setEditVersion(w.version);
                    setEdit(c);
                  }}
                >
                  Edit record
                </button>
              </div>
            </article>
          ))}
        {!w.changes.some(
          (c) => !linkedComment || c.comment_ids.includes(linkedComment),
        ) && (
          <Empty title="No changes recorded">
            Compare two documents below, then link each confirmed change to its
            comment.
          </Empty>
        )}
      </div>
      {w.documents.filter((d) => !d.email).length < 2 ? (
        <div className="record-comparison">
          <h3>Compare revisions</h3>
          <p className="record-note">
            Add the next drawing revision using Add documents. Rivet keeps both
            originals and their comments available for comparison.
          </p>
        </div>
      ) : (
        <div className="record-comparison">
          <h3>Compare revisions</h3>
          <p className="record-note">
            Compare extracted text from two documents. Geometry and scanned
            content still require manual review.
          </p>
          <div className="form-row">
            <label>
              Previous document
              <select
                aria-label="Previous document"
                value={before}
                onChange={(e) => setBefore(e.target.value)}
              >
                <option value="">Select document</option>
                {w.documents
                  .filter((d) => !d.email)
                  .map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                      {d.revision_label ? ` · Rev ${d.revision_label}` : ""}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              New document
              <select
                aria-label="New document"
                value={after}
                onChange={(e) => setAfter(e.target.value)}
              >
                <option value="">Select document</option>
                {w.documents
                  .filter((d) => !d.email)
                  .map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                      {d.revision_label ? ` · Rev ${d.revision_label}` : ""}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          {before && before === after && (
            <ErrorNote message="Select two different documents." />
          )}
          {compared.isFetching && <Busy text="Comparing extracted text…" />}
          {compared.isError && (
            <ErrorNote message={(compared.error as Error).message} />
          )}{" "}
          {compared.data && (
            <>
              <p className="record-note">
                {compared.data.changes.length} text changes detected
              </p>
              {compared.data.changes.map((c, i) => (
                <div className="record-diff" key={i}>
                  <div>
                    <small>Previous</small>
                    <p>{c.before || "—"}</p>
                    {c.before_source_ids.slice(0, 2).map((s) => (
                      <button
                        className="text-button"
                        onClick={() => openSource(s)}
                        key={s}
                      >
                        View source <ArrowUpRight size={13} />
                      </button>
                    ))}
                  </div>
                  <div>
                    <small>New</small>
                    <p>{c.after || "—"}</p>
                    {c.after_source_ids.slice(0, 2).map((s) => (
                      <button
                        className="text-button"
                        onClick={() => openSource(s)}
                        key={s}
                      >
                        View source <ArrowUpRight size={13} />
                      </button>
                    ))}
                    <button
                      className="text-button"
                      onClick={() => {
                        setEditVersion(w.version);
                        setEdit({
                          before: c.before.slice(0, 2000),
                          after: c.after.slice(0, 2000) || "Removed",
                          from_revision:
                            w.documents.find((d) => d.id === before)
                              ?.revision_label || "",
                          to_revision:
                            w.documents.find((d) => d.id === after)
                              ?.revision_label || "",
                        });
                      }}
                    >
                      Record change <Plus size={13} />
                    </button>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}
      <h3 className="record-history-title">Record history</h3>
      <div className="record-timeline">
        {w.events
          .filter(
            (e) =>
              !linkedComment ||
              e.item_id === linkedComment ||
              w.changes.some(
                (c) =>
                  c.id === e.item_id && c.comment_ids.includes(linkedComment),
              ),
          )
          .map((e) => (
            <details key={e.id}>
              <summary>
                <Clock size={15} />
                <span>
                  <strong>{e.summary}</strong>
                  <small>
                    {e.actor} · {when(e.at)}
                  </small>
                </span>
              </summary>
              {e.reason && <p>{e.reason}</p>}
              {e.before && e.after ? (
                <div className="record-audit-diff">
                  {Object.entries(e.after as Record<string, unknown>)
                    .filter(
                      ([k, v]) =>
                        !["updated_at", "flags"].includes(k) &&
                        JSON.stringify(v) !==
                          JSON.stringify(
                            (e.before as Record<string, unknown>)[k],
                          ),
                    )
                    .map(([k, v]) => (
                      <p key={k}>
                        <strong>{k.replaceAll("_", " ")}</strong>
                        <span>
                          {String(
                            (e.before as Record<string, unknown>)[k] ??
                              "Not set",
                          )}{" "}
                          → {String(v ?? "Not set")}
                        </span>
                      </p>
                    ))}
                </div>
              ) : null}
            </details>
          ))}
      </div>
      {edit && (
        <Modal
          title={edit.id ? "Edit change record" : "Record a change"}
          onClose={() => setEdit(null)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const form = e.currentTarget;
              const data = values(form);
              const ids = new FormData(form).getAll("comment_ids");
              if (
                await save("/changes" + (edit.id ? "/" + edit.id : ""), {
                  ...data,
                  comment_ids: ids,
                  expected_version: editVersion,
                })
              )
                setEdit(null);
            }}
          >
            <label>
              Change summary
              <input
                name="title"
                required
                maxLength={240}
                defaultValue={edit.title}
              />
            </label>
            <div className="form-row">
              <label>
                From revision
                <input name="from_revision" defaultValue={edit.from_revision} />
              </label>
              <label>
                To revision
                <input name="to_revision" defaultValue={edit.to_revision} />
              </label>
            </div>
            <div className="form-row">
              <label>
                Before
                <textarea
                  name="before"
                  rows={3}
                  maxLength={2000}
                  defaultValue={edit.before}
                />
              </label>
              <label>
                After
                <textarea
                  name="after"
                  required
                  rows={3}
                  maxLength={2000}
                  defaultValue={edit.after}
                />
              </label>
            </div>
            <label>
              Related comments
              <select
                multiple
                name="comment_ids"
                defaultValue={edit.comment_ids ?? []}
                aria-label="Related comments"
              >
                {w.comments.map((c) => (
                  <option key={c.id} value={c.id}>
                    #{c.number} · {c.text.slice(0, 85)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Requested by
              <input name="requested_by" defaultValue={edit.requested_by} />
            </label>
            <div className="form-row">
              <label>
                Approved by
                <input
                  name="approved_by"
                  defaultValue={edit.approved_by}
                  placeholder="Leave blank if not approved"
                />
              </label>
              <label>
                Approval date
                <input
                  name="approved_at"
                  type="date"
                  defaultValue={edit.approved_at}
                />
              </label>
            </div>
            <AuditFields />
            {error && <ErrorNote message={error} />}
            <footer className="modal-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => setEdit(null)}
              >
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                Save change record
              </button>
            </footer>
          </form>
        </Modal>
      )}
    </section>
  );
}
function Sharing({
  w,
  busy,
  save,
  notify,
  refresh,
  saveError,
}: {
  w: RecordView;
  saveError: string;
  busy: boolean;
  save: (p: string, b: unknown) => Promise<boolean>;
  notify: (s: string) => void;
  refresh: () => void;
}) {
  const [modal, setModal] = useState<"approval" | "recipient" | null>(null),
    [shareUrl, setShareUrl] = useState(""),
    [error, setError] = useState("");
  const [editVersion, setEditVersion] = useState(w.version);
  const latest = w.approvals.at(-1);
  const unreviewed = w.comments.filter((c) => !c.reviewed).length;
  return (
    <section>
      <div className="ow-section-heading">
        <div>
          <h2>Approved record</h2>
          <p>
            A saved version of the comment and change log for customer and
            production review.
          </p>
        </div>
        <button
          className="primary"
          disabled={!w.comments.length || unreviewed > 0}
          onClick={() => {
            setEditVersion(w.version);
            setModal("approval");
          }}
        >
          <CheckCircle size={16} />
          Approve record
        </button>
      </div>
      {unreviewed > 0 && (
        <p className="record-unclear">
          <WarningCircle size={16} />
          {unreviewed} comments need review before approval.
        </p>
      )}
      {latest ? (
        <div className="record-approval">
          <CheckCircle size={28} weight="duotone" />
          <div>
            <h3>{latest.label}</h3>
            <p>
              {latest.actor} · {when(latest.at)} · Record v{latest.version}
            </p>
            <p>{latest.reason}</p>
            <small>
              {w.version > latest.version
                ? "The working record has updates after this approval. Existing links keep the approved version."
                : "This approval matches the current working record."}
            </small>
          </div>
          <button
            className="secondary"
            disabled={busy}
            onClick={async () => {
              try {
                const result = await api<{ token: string }>(
                  `/orders/${w.order.id}/record/shares`,
                  {
                    expected_version: w.version,
                    approval_id: latest.id,
                    actor: latest.actor,
                    reason: "Create a read-only link to this approved record.",
                  },
                );
                setShareUrl(`${location.origin}/#shared/${result.token}`);
                refresh();
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            <LinkSimple size={16} />
            Create read-only link
          </button>
        </div>
      ) : (
        <Empty title="No approved record">
          Review extracted fields, then approve a version of the log. Open
          comments can remain visible in an approved record.
        </Empty>
      )}
      <p className="record-note">
        Approval records the reviewed log; equipment design and production
        release remain separate. Referenced original files are included in the
        read-only view. This local version’s links work on this computer; remote
        access requires hosting. Links expire after 30 days.
      </p>
      {shareUrl && (
        <div className="record-share-url">
          <input aria-label="Read-only record link" value={shareUrl} readOnly />
          <button
            className="secondary"
            onClick={() => {
              void navigator.clipboard
                .writeText(shareUrl)
                .then(() => notify("Read-only link copied"))
                .catch(() => setError("Select and copy the link manually."));
            }}
          >
            Copy link
          </button>
          <a href={shareUrl} target="_blank" rel="noreferrer">
            Preview <ArrowUpRight size={14} />
          </a>
        </div>
      )}
      {error && <ErrorNote message={error} />}
      <div className="record-share-list">
        {w.shares.map((s) => (
          <div key={s.id}>
            <span>
              {s.label}
              <small>
                {s.revoked ? "Revoked" : `Expires ${when(s.expires_at)}`}
              </small>
            </span>
            {!s.revoked && (
              <button
                className="text-button"
                onClick={async () => {
                  if (
                    await save(`/shares/${s.id}/revoke`, {
                      expected_version: w.version,
                      actor: "Local PM",
                      reason: "Revoked the read-only record link.",
                    })
                  )
                    setShareUrl("");
                }}
              >
                Revoke link
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="record-sharing-grid">
        <section>
          <div className="ow-section-heading">
            <div>
              <h3>Notice recipients</h3>
              <p>Include the customer team and production.</p>
            </div>
            <button
              className="text-button"
              onClick={() => {
                setEditVersion(w.version);
                setModal("recipient");
              }}
            >
              <Plus size={15} />
              Add recipient
            </button>
          </div>
          {w.subscribers.map((p) => (
            <div className="record-recipient" key={p.id}>
              <span>
                <strong>{p.name}</strong>
                <small>
                  {p.email} · {p.team}
                </small>
              </span>
              <button
                className="icon-button"
                aria-label={`Remove ${p.name}`}
                onClick={() =>
                  void save(`/subscribers/${p.id}/remove`, {
                    expected_version: w.version,
                    actor: "Local PM",
                    reason: "Removed from future notice drafts.",
                  })
                }
              >
                <X size={15} />
              </button>
            </div>
          ))}
          {!w.subscribers.length && (
            <p className="record-note">
              No recipients yet. Changes create notice drafts for the people
              listed here.
            </p>
          )}
        </section>
        <section>
          <h3>Change notices</h3>
          <p className="record-note">
            Drafts capture the recipients at the time of a change. Review each
            message before sending through Rivet or your email app.
          </p>
          {w.notices.map((n) => (
            <details className="record-notice" key={n.id}>
              <summary>
                <span>
                  {n.title}
                  <small>{n.recipients.length} recipients · Notice draft</small>
                </span>
              </summary>
              <p>{n.body}</p>
              <p>{n.recipients.map((r) => r.email).join(", ")}</p>
              <a
                className="text-button"
                href={`/api/orders/${w.order.id}/record/notices/${n.id}/draft`}
              >
                <DownloadSimple size={15} />
                Download email draft
              </a>
              <SendNoticeButton w={w} notice={n} />
            </details>
          ))}
          {!w.notices.length && (
            <p className="record-note">No notice drafts.</p>
          )}
        </section>
      </div>
      <Exports id={w.order.id} />
      {modal && (
        <Modal
          title={
            modal === "approval" ? "Approve the record" : "Add notice recipient"
          }
          onClose={() => setModal(null)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (
                await save(
                  modal === "approval" ? "/approvals" : "/subscribers",
                  { ...values(e.currentTarget), expected_version: editVersion },
                )
              )
                setModal(null);
            }}
          >
            {modal === "approval" ? (
              <>
                <p className="modal-intro">
                  Save the current comments, responses, and change history as an
                  approved record. Later edits remain separate.
                </p>
                <label>
                  Approval label
                  <input
                    name="label"
                    required
                    maxLength={180}
                    placeholder="e.g. Revision 02 · reviewed comment log"
                  />
                </label>
              </>
            ) : (
              <>
                <label>
                  Name
                  <input name="name" required maxLength={180} />
                </label>
                <label>
                  Email
                  <input name="email" type="email" required maxLength={254} />
                </label>
                <label>
                  Team
                  <select name="team">
                    <option>Manufacturer</option>
                    <option>Customer</option>
                    <option>Production</option>
                  </select>
                </label>
              </>
            )}
            <AuditFields />
            {saveError && <ErrorNote message={saveError} />}
            <footer className="modal-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => setModal(null)}
              >
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                {modal === "approval" ? "Approve record" : "Add recipient"}
              </button>
            </footer>
          </form>
        </Modal>
      )}
    </section>
  );
}
export function SharedRecord({ token }: { token: string }) {
  const query = useQuery({
    queryKey: ["shared-record", token],
    queryFn: () =>
      api<
        Pick<
          RecordView,
          "order" | "comments" | "changes" | "sources" | "documents"
        > & { approval: RecordView["approvals"][number] }
      >(`/shared/${token}`),
    retry: false,
  });
  const [source, setSource] = useState("");
  const w = query.data;
  const sp = w?.sources.find((s) => s.id === source);
  const doc = w?.documents.find((d) => d.id === (sp?.document_id || source));
  return (
    <main className="record-public">
      <header>
        <span className="brand">rivet.</span>
        <span>Read-only record</span>
      </header>
      {query.isError ? (
        <ErrorNote message={(query.error as Error).message} />
      ) : !w ? (
        <Busy text="Opening approved record…" />
      ) : (
        <>
          <div className="record-public-heading">
            <span className="eyebrow">
              {w.order.number} · {w.order.customer}
            </span>
            <h1>{w.order.title}</h1>
            <h2>{w.approval.label}</h2>
            <p>
              Approved by {w.approval.actor} · {when(w.approval.at)}
            </p>
            <p>{w.approval.reason}</p>
            <small>
              Approved record v{w.approval.version}. Subsequent working edits
              are not included.
            </small>
          </div>
          <h2>Comment log</h2>
          {w.comments.map((c) => (
            <article className="record-public-comment" key={c.id}>
              <div>
                <strong>
                  #{c.number} · Rev {c.revision}
                </strong>
                <span className={"record-status " + c.status}>{c.status}</span>
              </div>
              <p>{c.text}</p>
              <small>
                {c.author || "Author not stated"} ·{" "}
                {c.authored_at || "Date not stated"}
              </small>
              {c.response && (
                <blockquote>
                  <span>Manufacturer response · {c.responder}</span>
                  <p>{c.response}</p>
                </blockquote>
              )}
              <div className="record-tools">
                {c.source_ids.map((s) => (
                  <button
                    key={s}
                    className="text-button"
                    onClick={() => setSource(s)}
                  >
                    Original source <ArrowUpRight size={13} />
                  </button>
                ))}
                {c.target_source_id && (
                  <button
                    className="text-button"
                    onClick={() => setSource(c.target_source_id)}
                  >
                    Drawing location <ArrowUpRight size={13} />
                  </button>
                )}
              </div>
            </article>
          ))}
          <h2>Recorded changes</h2>
          {w.changes.length ? (
            w.changes.map((c) => (
              <article className="record-public-comment" key={c.id}>
                <h3>{c.title}</h3>
                <p>
                  {c.before || "Not stated"} → {c.after}
                </p>
                <small>
                  Requested by {c.requested_by || "not recorded"} · Approved by{" "}
                  {c.approved_by || "not recorded"} {c.approved_at}
                </small>
              </article>
            ))
          ) : (
            <p>No changes recorded in this version.</p>
          )}
          <footer>
            This record documents communication and approvals. It does not
            certify equipment compliance.
          </footer>
          {doc && (
            <OrderEvidence
              key={source}
              document={doc}
              sources={w.sources.filter((s) => s.document_id === doc.id)}
              selectedId={sp?.id}
              contentUrl={`/api/shared/${token}/documents/${doc.id}`}
              close={() => setSource("")}
            />
          )}
        </>
      )}
    </main>
  );
}
function RecordAssistant({
  w,
  openSource,
  openComment,
  openChanges,
}: {
  w: RecordView;
  openSource: (s: string) => void;
  openComment: (s: string) => void;
  openChanges: () => void;
}) {
  type Answer = {
    answer: string;
    source_ids: string[];
    comment_ids: string[];
    change_ids: string[];
    version: number;
  };
  const [prompt, setPrompt] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [messages, setMessages] = useState<{ question: string; result: Answer }[]>(
      [],
    ),
    [dismissed, setDismissed] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        input.current?.focus();
        input.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  async function ask(question: string) {
    if (!question.trim() || busy) return;
    setBusy(true);
    setError("");
    setDismissed(false);
    try {
      const result = await api<Answer>(`/orders/${w.order.id}/record/ask`, {
        question,
        history: messages.slice(-3).flatMap((m) => [
          { role: "user", content: m.question.slice(0, 6000) },
          { role: "assistant", content: m.result.answer.slice(0, 6000) },
        ]),
      });
      setMessages((prev) => [...prev, { question, result }]);
      setPrompt("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const latest = messages.at(-1);
  return (
    <section className="ow-command" aria-label="Rivet command bar">
      <div className="ow-command-top">
        <div>
          <Mark small />
          <strong>Rivet</strong>
          <span>Ask about the order. Follow the source.</span>
        </div>
        <kbd>⌘ K</kbd>
      </div>
      {latest && !dismissed && (
        <div className="ow-command-answer" aria-live="polite">
          <button
            className="icon-button ow-dismiss-answer"
            aria-label="Dismiss answer"
            onClick={() => setDismissed(true)}
          >
            <X size={15} />
          </button>
          <p>{latest.result.answer}</p>
          <div className="record-answer-links">
            {latest.result.comment_ids.map((id) => (
              <button key={id} onClick={() => openComment(id)}>
                Comment {w.comments.find((c) => c.id === id)?.number}{" "}
                <ArrowUpRight size={12} />
              </button>
            ))}
            {latest.result.source_ids.map((id) => {
              const source = w.sources.find((s) => s.id === id);
              return (
                <button key={id} onClick={() => openSource(id)}>
                  {w.documents.find((d) => d.id === source?.document_id)
                    ?.name ?? "Source"}
                  {source?.location.page ? ` · p. ${source.location.page}` : ""}{" "}
                  <ArrowUpRight size={12} />
                </button>
              );
            })}
            {latest.result.change_ids.map((id) => (
              <button key={id} onClick={openChanges}>
                {w.changes.find((c) => c.id === id)?.title || "Change record"}{" "}
                <ArrowUpRight size={12} />
              </button>
            ))}
          </div>
          <small className="record-answer-version">
            Answered from record v{latest.result.version}
            {latest.result.version !== w.version
              ? " · The record has since changed."
              : ""}
          </small>
        </div>
      )}
      {error && <ErrorNote message={error} />}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask(prompt);
        }}
      >
        <textarea
          ref={input}
          rows={1}
          aria-label="Ask Rivet about this order"
          placeholder="What changed? Who needs to respond?"
          maxLength={6000}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask(prompt);
            }
          }}
        />
        <button
          className="ow-command-send"
          disabled={busy || !prompt.trim()}
          aria-label="Send question"
        >
          {busy ? <Busy text="" /> : <ArrowRight size={19} />}
        </button>
      </form>
      <div className="ow-command-examples">
        {[
          "What changed since the previous revision?",
          "Which comments need a response?",
          "What is unclear?",
        ].map((q) => (
          <button
            key={q}
            disabled={busy}
            onClick={() => {
              setPrompt(q);
              input.current?.focus();
            }}
          >
            {q}
            <ArrowUpRight size={11} />
          </button>
        ))}
      </div>
    </section>
  );
}
