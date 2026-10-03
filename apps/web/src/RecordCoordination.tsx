import { useState } from "react";
import {
  ArrowCounterClockwise,
  ArrowRight,
  ArrowUpRight,
  ArrowsClockwise,
  CalendarBlank,
  ChatText,
  Check,
  CheckCircle,
  Clock,
  FileText,
  LinkSimple,
  ListChecks,
  PencilSimple,
  SlidersHorizontal,
  Users,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { useAccount } from "./Auth";
import { when } from "./api";
import { Empty, ErrorNote, Modal } from "./ui";
import type {
  CoordinationCheck,
  CoordinationRole,
  CoordinationSettings,
  CoordinationSuggestion,
  CoordinationTask,
  RecordView,
} from "./record-types";
import "./record-coordination.css";

const roleNames: Record<CoordinationRole, string> = {
  pm: "Project manager",
  drafting: "Drafting / engineering",
  production: "Production",
  commercial: "Commercial",
};
type Save = (path: string, body: unknown) => Promise<boolean>;
type View = "suggestions" | "tasks" | "readiness" | "references";

export function RecordCoordination({
  w,
  busy,
  error,
  save,
  openSource,
  openComment,
  openSharing,
  addDocuments,
}: {
  w: RecordView;
  busy: boolean;
  error: string;
  save: Save;
  openSource: (id: string) => void;
  openComment: (id: string) => void;
  openSharing: () => void;
  addDocuments: () => void;
}) {
  const account = useAccount();
  const [view, setView] = useState<View>("suggestions");
  const [showHistory, setShowHistory] = useState(false);
  const [settings, setSettings] = useState(false);
  const c = w.coordination;
  const write: Write = (path, reason, fields = {}, version = w.version) =>
    save(`/coordination${path}`, {
      expected_version: version,
      actor: account.name || "Workspace user",
      reason,
      ...fields,
    });
  if (!c)
    return (
      <Empty
        title="Prepare the work queue"
        icon={<ListChecks size={27} />}
        action={
          <button
            className="secondary"
            disabled={busy}
            onClick={() =>
              void write("/run", "Check order references and readiness")
            }
          >
            Check record
          </button>
        }
      >
        Add the order’s documents, then check its references and open work.
      </Empty>
    );
  const pending = c.suggestions.filter((s) => s.status === "pending");
  const tasks = c.tasks.filter((t) => t.status !== "done");
  const accepted = c.metrics.accepted_unchanged + c.metrics.accepted_edited;
  return (
    <section
      className="coordination coordination-focused"
      aria-label="Order work queue"
    >
      <div className="coordination-intro">
        <div>
          <h2>Work queue</h2>
          {c.settings.customer_due_date && (
            <span className="coordination-due">
              <CalendarBlank size={14} />
              Review due{" "}
              {new Date(
                `${c.settings.customer_due_date}T12:00:00`,
              ).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
            </span>
          )}
        </div>
        <button
          className="text-button"
          title="Owners, due date, and weekly draft"
          onClick={() => setSettings(true)}
        >
          <SlidersHorizontal size={15} />
          Configure
        </button>
      </div>
      <div className="coordination-toolbar">
        <div
          className="coordination-views"
          role="group"
          aria-label="Work queue views"
        >
          {(
            [
              ["suggestions", "Suggestions", pending.length],
              ["tasks", "Assigned work", tasks.length],
              ["readiness", "Readiness", null],
              ["references", "References", c.anchors.length],
            ] as const
          ).map(([key, label, count]) => (
            <button
              key={key}
              className={view === key ? "active" : ""}
              aria-pressed={view === key}
              onClick={() => {
                setView(key);
                setShowHistory(false);
              }}
            >
              {label}
              {count !== null && <span>{count}</span>}
            </button>
          ))}
        </div>
        {(view === "suggestions" || view === "tasks") && (
          <label className="coordination-history-toggle">
            <input
              type="checkbox"
              checked={showHistory}
              onChange={(e) => setShowHistory(e.target.checked)}
            />
            Include {view === "tasks" ? "completed" : "resolved"}
          </label>
        )}
      </div>
      {view === "suggestions" && (
        <div className="coordination-list">
          {(showHistory ? c.suggestions : pending).map((s) => (
            <Suggestion
              key={`${s.id}:${s.status}`}
              suggestion={s}
              w={w}
              busy={busy}
              write={write}
              openSource={openSource}
              openComment={openComment}
              openSharing={openSharing}
            />
          ))}
          {!(showHistory ? c.suggestions : pending).length && (
            <Empty
              title={
                w.documents.length
                  ? "No suggestions to review"
                  : "Add documents to begin"
              }
              icon={<CheckCircle size={25} />}
              action={
                !w.documents.length ? (
                  <button className="secondary" onClick={addDocuments}>
                    Add documents
                  </button>
                ) : (
                  <button
                    className="text-button"
                    onClick={() => setView("readiness")}
                  >
                    Check readiness <ArrowRight size={14} />
                  </button>
                )
              }
            >
              {w.documents.some((d) => d.state === "queued")
                ? "Your documents are processing. Suggestions appear when the record is ready."
                : "Source links and follow-ups are prepared here. The activity feed in Rivet shows what has been recorded."}
            </Empty>
          )}
        </div>
      )}
      {view === "tasks" && (
        <div className="coordination-list">
          {(showHistory ? c.tasks : tasks).map((task) => (
            <Task
              key={task.id}
              task={task}
              w={w}
              busy={busy}
              write={write}
              openComment={openComment}
              openSource={openSource}
            />
          ))}
          {!(showHistory ? c.tasks : tasks).length && (
            <Empty
              title="No outstanding assignments"
              icon={<Users size={25} />}
            >
              Assignments appear when the record identifies work for a project
              role. Configure owners to route new work.
            </Empty>
          )}
        </div>
      )}
      {view === "readiness" && (
        <div className="coordination-readiness-view">
          <p>
            Checks against recorded information. Engineering acceptance remains
            with your team.
          </p>
          <div>
            <Readiness
              title="For resubmittal"
              checks={c.readiness.resubmit}
              w={w}
              openComment={openComment}
              openSource={openSource}
            />
            <Readiness
              title="For release"
              checks={c.readiness.release}
              w={w}
              openComment={openComment}
              openSource={openSource}
            />
          </div>
          <button className="text-button" onClick={openSharing}>
            Approved record & recipients <ArrowRight size={14} />
          </button>
        </div>
      )}
      {view === "references" && (
        <AnchorIndex
          w={w}
          openSource={openSource}
          openComment={openComment}
          expanded
        />
      )}
      <div className="coordination-footnote">
        <span>
          {c.last_run_at
            ? `Checked ${when(c.last_run_at)}`
            : "Checks run when information arrives."}
        </span>
        {accepted > 0 && (
          <span>
            {c.metrics.accepted_unchanged} accepted unchanged ·{" "}
            {c.metrics.accepted_edited} edited
          </span>
        )}
      </div>
      {settings && (
        <Settings
          settings={c.settings}
          version={w.version}
          busy={busy}
          error={error}
          close={() => setSettings(false)}
          write={write}
        />
      )}
    </section>
  );
}

type Write = (
  path: string,
  reason: string,
  fields?: object,
  version?: number,
) => Promise<boolean>;
function Suggestion({
  suggestion: s,
  w,
  busy,
  write,
  openSource,
  openComment,
  openSharing,
}: {
  suggestion: CoordinationSuggestion;
  w: RecordView;
  busy: boolean;
  write: Write;
  openSource: (id: string) => void;
  openComment: (id: string) => void;
  openSharing: () => void;
}) {
  const [edit, setEdit] = useState(false);
  const [version, setVersion] = useState(w.version);
  const [draft, setDraft] = useState(s.draft);
  const [target, setTarget] = useState(s.target_source_id);
  const [reason, setReason] = useState(
    s.kind === "drawing_link"
      ? "Reviewed the cited source and drawing reference"
      : "Reviewed the prepared draft",
  );
  const link = s.kind === "drawing_link";
  const pending = s.status === "pending";
  const candidate = s.candidates.find(
    (x) => x.source_id === s.target_source_id,
  );
  const selectedComment = w.comments.find((x) => x.id === s.comment_id);
  const fields = edit ? { target_source_id: target, draft } : {};
  const beginEdit = () => {
    setVersion(w.version);
    setDraft(s.draft);
    setTarget(s.target_source_id);
    setEdit(true);
  };
  async function accept() {
    if (
      await write(
        `/suggestions/${s.id}`,
        reason,
        { action: "accept", ...fields },
        edit ? version : w.version,
      )
    )
      setEdit(false);
  }
  return (
    <article className="coordination-suggestion">
      <details className="coordination-review-row">
        <summary>
          <span className="coordination-row-icon">
            {link ? <LinkSimple size={18} /> : <ChatText size={18} />}
          </span>
          <span className="coordination-row-title">
            <strong>{s.title}</strong>
            <span className="coordination-row-preview">
              {selectedComment?.text || s.reason}
            </span>
          </span>
          <span className="coordination-row-action">
            {pending ? "Review" : s.status} <ArrowRight size={13} />
          </span>
        </summary>
        <div className="coordination-row-detail">
          <div className="coordination-kind">
            <span>
              {link ? <LinkSimple size={14} /> : <ChatText size={14} />}
              {s.kind.replaceAll("_", " ")}
            </span>
            <span
              className={
                s.confidence === "explicit" ? "is-explicit" : "needs-human"
              }
            >
              {s.confidence === "explicit"
                ? "Explicit reference"
                : "Needs review"}
            </span>
            {!pending && (
              <span>{s.status === "stale" ? "Out of date" : s.status}</span>
            )}
          </div>

          <p>{s.reason}</p>
          {selectedComment && (
            <blockquote>
              <span>Comment {selectedComment.number} · Original text</span>
              {selectedComment.original_text || selectedComment.text}
            </blockquote>
          )}
          {link && candidate && (
            <button
              className="coordination-proposed-link"
              onClick={() => openSource(candidate.source_id)}
            >
              <FileText size={17} />
              <span>
                <strong>{candidate.label}</strong>
                <small>{candidate.reason}</small>
              </span>
              <ArrowUpRight size={15} />
            </button>
          )}
          {!link && s.draft && !edit && (
            <details className="coordination-draft">
              <summary>
                Prepared draft <span>Not sent</span>
              </summary>
              <p>{s.draft}</p>
            </details>
          )}
          <EvidenceLinks
            w={w}
            commentId={s.comment_id}
            sourceIds={s.source_ids}
            openSource={openSource}
            openComment={openComment}
          />
          {edit && (
            <form
              className="coordination-editor"
              onSubmit={(e) => {
                e.preventDefault();
                void accept();
              }}
            >
              {link ? (
                <label>
                  Drawing reference
                  <select
                    required
                    value={target}
                    onChange={(e) => setTarget(e.target.value)}
                  >
                    <option value="">Select a source</option>
                    {s.candidates.map((x) => (
                      <option value={x.source_id} key={x.source_id}>
                        {x.label}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <label>
                  Draft for review
                  <textarea
                    required
                    rows={7}
                    maxLength={20000}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                  />
                </label>
              )}
              <label>
                Reason for the record
                <input
                  required
                  minLength={3}
                  maxLength={2000}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <div className="coordination-actions">
                <button className="primary" disabled={busy} type="submit">
                  <Check size={14} />
                  {link ? "Save link" : "Save draft"}
                </button>
                <button
                  className="text-button"
                  type="button"
                  disabled={busy}
                  onClick={() => setEdit(false)}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
          {pending && !edit && (
            <div className="coordination-actions">
              <button
                className="primary"
                disabled={busy}
                onClick={() =>
                  link && !s.target_source_id ? beginEdit() : void accept()
                }
              >
                {link && !s.target_source_id ? (
                  <LinkSimple size={14} />
                ) : (
                  <Check size={14} />
                )}
                {link
                  ? s.target_source_id
                    ? "Accept link"
                    : "Choose drawing"
                  : "Accept draft"}
              </button>
              {(!link || s.target_source_id) && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={beginEdit}
                >
                  <PencilSimple size={14} />
                  Edit
                </button>
              )}
              <button
                className="text-button coordination-skip"
                disabled={busy}
                onClick={() =>
                  void write(
                    `/suggestions/${s.id}`,
                    "Suggestion skipped after review",
                    { action: "skip" },
                  )
                }
              >
                Skip <X size={13} />
              </button>
            </div>
          )}
          {!pending && s.status === "accepted" && !link && (
            <button className="text-button" onClick={openSharing}>
              Open saved drafts <ArrowRight size={14} />
            </button>
          )}
        </div>
      </details>
    </article>
  );
}

function Task({
  task,
  w,
  busy,
  write,
  openComment,
  openSource,
}: {
  task: CoordinationTask;
  w: RecordView;
  busy: boolean;
  write: Write;
  openComment: (id: string) => void;
  openSource: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [version, setVersion] = useState(w.version);
  return (
    <article className="coordination-task">
      <div className="coordination-task-top">
        <span className="coordination-task-role">
          <Users size={14} />
          {roleNames[task.role]}
        </span>
        <span
          className={`record-status ${task.status === "done" ? "closed" : "open"}`}
        >
          {task.status.replaceAll("_", " ")}
        </span>
      </div>
      <h3>{task.title}</h3>
      <p>{task.reason}</p>
      <div className="coordination-task-owner">
        {task.owner || "No owner assigned"}
      </div>
      {task.note && <p className="coordination-task-note">{task.note}</p>}
      <EvidenceLinks
        w={w}
        commentId={task.comment_id}
        sourceIds={task.source_ids}
        openComment={openComment}
        openSource={openSource}
      />
      {editing ? (
        <form
          className="coordination-editor"
          onSubmit={async (e) => {
            e.preventDefault();
            const data = Object.fromEntries(new FormData(e.currentTarget));
            if (
              await write(
                `/tasks/${task.id}`,
                String(data.reason),
                data,
                version,
              )
            )
              setEditing(false);
          }}
        >
          <div className="form-row">
            <label>
              Role
              <select name="role" defaultValue={task.role}>
                {Object.entries(roleNames).map(([key, name]) => (
                  <option key={key} value={key}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Owner
              <input
                name="owner"
                maxLength={120}
                defaultValue={task.owner}
                placeholder="Name or team"
              />
            </label>
          </div>
          <label>
            Status
            <select name="status" defaultValue={task.status}>
              <option value="open">Open</option>
              <option value="in_progress">In progress</option>
              <option value="done">Done</option>
            </select>
          </label>
          <label>
            Work note
            <textarea
              name="note"
              maxLength={4000}
              rows={3}
              defaultValue={task.note}
            />
          </label>
          <label>
            Reason
            <input
              name="reason"
              required
              minLength={3}
              maxLength={2000}
              defaultValue="Update assignment and work status"
            />
          </label>
          <p className="coordination-editor-note">
            Completing an assignment does not close its comment or approve
            engineering work.
          </p>
          <div className="coordination-actions">
            <button type="submit" className="primary" disabled={busy}>
              Save assignment
            </button>
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <button
          className="text-button"
          disabled={busy}
          onClick={() => {
            setVersion(w.version);
            setEditing(true);
          }}
        >
          <PencilSimple size={14} />
          Update assignment
        </button>
      )}
    </article>
  );
}

function EvidenceLinks({
  w,
  commentId,
  sourceIds,
  openComment,
  openSource,
}: {
  w: RecordView;
  commentId: string;
  sourceIds: string[];
  openComment: (id: string) => void;
  openSource: (id: string) => void;
}) {
  const comment = w.comments.find((x) => x.id === commentId);
  const sources = [...new Set(sourceIds)]
    .map((id) => w.sources.find((s) => s.id === id))
    .filter((s) => !!s);
  if (!comment && !sources.length) return null;
  return (
    <div className="coordination-evidence">
      {comment && (
        <button className="text-button" onClick={() => openComment(comment.id)}>
          <ChatText size={13} />
          Comment {comment.number}
          <ArrowUpRight size={11} />
        </button>
      )}
      {sources.slice(0, 4).map((s) => (
        <button
          className="text-button"
          key={s.id}
          onClick={() => openSource(s.id)}
          title={w.documents.find((d) => d.id === s.document_id)?.name}
        >
          <FileText size={13} />
          <span>
            {w.documents.find((d) => d.id === s.document_id)?.name || "Source"}
            {s.location.page
              ? ` · p. ${s.location.page}`
              : s.location.line
                ? ` · line ${s.location.line}`
                : s.location.cells
                  ? ` · ${s.location.cells}`
                  : ""}
          </span>
          <ArrowUpRight size={11} />
        </button>
      ))}
      {sources.length > 4 && (
        <details>
          <summary>{sources.length - 4} more sources</summary>
          {sources.slice(4).map((s) => (
            <button
              className="text-button"
              key={s.id}
              onClick={() => openSource(s.id)}
            >
              {w.documents.find((d) => d.id === s.document_id)?.name}
              {s.location.page ? ` · p. ${s.location.page}` : ""}
              <ArrowUpRight size={11} />
            </button>
          ))}
        </details>
      )}
    </div>
  );
}

function Readiness({
  title,
  checks,
  w,
  openComment,
  openSource,
}: {
  title: string;
  checks: CoordinationCheck[];
  w: RecordView;
  openComment: (id: string) => void;
  openSource: (id: string) => void;
}) {
  return (
    <section className="coordination-readiness">
      <h4>
        {title}
        <span>
          {checks.filter((c) => c.status === "pass").length}/{checks.length}
        </span>
      </h4>
      {checks.map((check) => (
        <details key={check.id}>
          <summary>
            <span
              className={
                check.status === "pass"
                  ? "coordination-check-pass"
                  : "coordination-check-review"
              }
            >
              {check.status === "pass" ? (
                <CheckCircle size={17} />
              ) : (
                <WarningCircle size={17} />
              )}
            </span>
            <span>{check.label}</span>
            <small>
              {check.status === "pass"
                ? "Recorded"
                : check.status === "blocked"
                  ? "Open"
                  : "Review"}
            </small>
          </summary>
          <p>{check.detail}</p>
          {check.comment_ids.map((id) => (
            <button
              className="text-button"
              key={id}
              onClick={() => openComment(id)}
            >
              Comment {w.comments.find((c) => c.id === id)?.number || "record"}
              <ArrowUpRight size={12} />
            </button>
          ))}
          <EvidenceLinks
            w={w}
            commentId=""
            sourceIds={check.source_ids}
            openComment={openComment}
            openSource={openSource}
          />
        </details>
      ))}
      {!checks.length && <p>No checks prepared yet.</p>}
    </section>
  );
}

function AnchorIndex({
  w,
  openSource,
  openComment,
  expanded = false,
}: {
  expanded?: boolean;
  w: RecordView;
  openSource: (id: string) => void;
  openComment: (id: string) => void;
}) {
  const [search, setSearch] = useState("");
  const anchors = w.coordination?.anchors ?? [];
  return (
    <details className="coordination-anchors" open={expanded || undefined}>
      <summary>
        <LinkSimple size={15} />
        Connected references<span>{anchors.length}</span>
      </summary>
      <p>
        Exact identifiers found in this order. Shared references connect
        sources; they do not establish approval or an implemented design change.
      </p>
      <input
        aria-label="Search connected references"
        placeholder="Find a tag, sheet, or comment…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <div className="coordination-anchor-list">
        {anchors
          .filter((a) =>
            `${a.value} ${a.kind}`.toLowerCase().includes(search.toLowerCase()),
          )
          .map((anchor) => (
            <details key={anchor.id}>
              <summary>
                <strong>{anchor.value}</strong>
                <small>{anchor.kind.replaceAll("_", " ")}</small>
              </summary>
              <EvidenceLinks
                w={w}
                commentId=""
                sourceIds={anchor.source_ids}
                openComment={openComment}
                openSource={openSource}
              />
              {anchor.comment_ids.map((id) => (
                <button
                  className="text-button"
                  key={id}
                  onClick={() => openComment(id)}
                >
                  Comment{" "}
                  {w.comments.find((c) => c.id === id)?.number || "record"}
                  <ArrowUpRight size={12} />
                </button>
              ))}
            </details>
          ))}
      </div>
      {!anchors.length && <p>No explicit identifiers extracted yet.</p>}
    </details>
  );
}

function Settings({
  settings,
  version,
  busy,
  error,
  close,
  write,
}: {
  settings: CoordinationSettings;
  version: number;
  busy: boolean;
  error: string;
  close: () => void;
  write: Write;
}) {
  const [editVersion] = useState(version);
  return (
    <Modal
      title="Order coordination"
      eyebrow="Owners & follow-ups"
      onClose={close}
    >
      {error && <ErrorNote message={error} />}
      <form
        className="coordination-settings"
        onSubmit={async (e) => {
          e.preventDefault();
          const form = new FormData(e.currentTarget);
          const role_owners = Object.fromEntries(
            Object.keys(roleNames).map((role) => [
              role,
              String(form.get(role) || ""),
            ]),
          );
          if (
            await write(
              "/settings",
              "Update order owners, review due date, and weekly draft preference",
              {
                role_owners,
                customer_due_date: String(form.get("customer_due_date") || ""),
                weekly_digest_enabled:
                  form.get("weekly_digest_enabled") === "on",
              },
              editVersion,
            )
          )
            close();
        }}
      >
        <p>
          Set the people responsible for each type of work. Assignments stay
          inside this order.
        </p>
        <div className="coordination-owner-grid">
          {Object.entries(roleNames).map(([role, name]) => (
            <label key={role}>
              {name}
              <input
                name={role}
                maxLength={120}
                defaultValue={
                  settings.role_owners[role as CoordinationRole] || ""
                }
                placeholder="Name or team"
              />
            </label>
          ))}
        </div>
        <label>
          Customer review due date
          <input
            type="date"
            name="customer_due_date"
            defaultValue={settings.customer_due_date}
          />
        </label>
        <label className="coordination-checkbox">
          <input
            type="checkbox"
            name="weekly_digest_enabled"
            defaultChecked={settings.weekly_digest_enabled}
          />
          <span>
            <strong>Prepare a weekly status draft</strong>
            <small>
              Summarize open comments and recorded changes for review. Nothing
              is emailed automatically.
            </small>
          </span>
        </label>
        <div className="coordination-actions">
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save settings"}
          </button>
          <button className="text-button" type="button" onClick={close}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}
