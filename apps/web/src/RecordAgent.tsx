import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  ArrowCounterClockwise,
  ArrowsClockwise,
  ChatText,
  Check,
  CircleNotch,
  FileText,
  LinkSimple,
  ListChecks,
  SidebarSimple,
  WarningCircle,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { useAccount } from "./Auth";
import { ErrorNote, Mark } from "./ui";
import type { RecordView } from "./record-types";

type Answer = {
  answer: string;
  source_ids: string[];
  comment_ids: string[];
  change_ids: string[];
  version: number;
};
type Message = { question: string; result: Answer };

export function RecordAgent({
  w,
  open,
  setOpen,
  save,
  saveError,
  recordBusy,
  focusedCommentId,
  focusedSourceId,
  openSource,
  openComment,
  openChanges,
  openWork,
}: {
  w: RecordView;
  open: boolean;
  setOpen: (value: boolean) => void;
  save: (path: string, body: unknown) => Promise<boolean>;
  saveError: string;
  recordBusy: boolean;
  focusedCommentId: string;
  focusedSourceId: string;
  openSource: (id: string) => void;
  openComment: (id: string) => void;
  openChanges: () => void;
  openWork: () => void;
}) {
  const account = useAccount();
  const [view, setView] = useState<"conversation" | "activity">("conversation");
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [asking, setAsking] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const [activeQuestion, setActiveQuestion] = useState("");
  const [drawer, setDrawer] = useState(
    () => window.matchMedia("(max-width: 1439px)").matches,
  );
  const input = useRef<HTMLTextAreaElement>(null);
  const panel = useRef<HTMLElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const queued = w.documents.filter((d) => d.state === "queued");
  const failed = w.documents.filter((d) => d.state === "failed");
  const pending =
    w.coordination?.suggestions.filter((s) => s.status === "pending") ?? [];
  const recordedActivity = w.coordination?.activity ?? [];
  const activity = [
    ...recordedActivity,
    ...w.events
      .filter(
        (event) =>
          event.kind === "intake" &&
          !recordedActivity.some((item) => item.id === event.id),
      )
      .map((event) => ({
        id: event.id,
        kind: "intake",
        title: event.summary,
        reason: event.reason,
        at: event.at,
        actor: event.actor,
        comment_id: "",
        source_ids: w.sources
          .filter(
            (source) =>
              source.document_id ===
              (typeof event.after === "object" &&
              event.after &&
              "document_id" in event.after
                ? event.after.document_id
                : event.item_id),
          )
          .map((source) => source.id)
          .slice(0, 4),
        undoable: false,
        undone_at: "",
      })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  const focused = w.comments.find((c) => c.id === focusedCommentId);
  const focusedSource = w.sources.find(
    (source) => source.id === focusedSourceId,
  );
  const focusedDocument = w.documents.find(
    (document) =>
      document.id === (focusedSource?.document_id || focusedSourceId),
  );
  const links = focused ? w.connections.comments[focused.id] : undefined;
  const working = queued.length > 0 || checking;
  const status = checking
    ? "Checking order record"
    : queued.length
      ? `${queued.length} ${queued.length === 1 ? "document" : "documents"} in queue`
      : failed.length
        ? `${failed.length} ${failed.length === 1 ? "document needs" : "documents need"} attention`
        : pending.length
          ? `${pending.length} suggestions ready`
          : w.coordination?.last_run_at
            ? "Record checked"
            : "Ready to check";
  const suggestions = focused
    ? [
        "What is this comment asking?",
        "Show the evidence connected to this comment.",
        "Is a response recorded?",
      ]
    : [
        "What needs my attention?",
        "What changed since approval?",
        "Which comments need a response?",
      ];
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1439px)");
    const changed = () => {
      setDrawer(media.matches);
      if (media.matches) setOpen(false);
    };
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, [setOpen]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key.toLowerCase() === "j" &&
        !document.querySelector(".modal-backdrop")
      ) {
        e.preventDefault();
        setOpen(true);
        setView("conversation");
        requestAnimationFrame(() => input.current?.focus());
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [setOpen]);
  useEffect(() => {
    if (!open || !drawer) return;
    const previous = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const elements = () =>
      Array.from(
        panel.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled),textarea,input,summary,[tabindex="0"]',
        ) ?? [],
      ).filter((e) => e.offsetParent !== null);
    (input.current ?? elements()[0])?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector(".modal-backdrop")) {
        e.preventDefault();
        setOpen(false);
      }
      if (e.key === "Tab" && !document.querySelector(".modal-backdrop")) {
        const items = elements();
        if (e.shiftKey && document.activeElement === items[0]) {
          e.preventDefault();
          items.at(-1)?.focus();
        } else if (!e.shiftKey && document.activeElement === items.at(-1)) {
          e.preventDefault();
          items[0]?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", key);
      if (previous?.isConnected) previous.focus();
    };
  }, [open, drawer, setOpen]);
  useEffect(() => {
    if (view === "conversation" && messages.length)
      end.current?.scrollIntoView({ block: "nearest" });
  }, [messages.length, view]);
  async function ask(question: string) {
    if (!question.trim() || asking) return;
    setAsking(true);
    setError("");
    setActiveQuestion(question);
    setPrompt("");
    const context = [
      focused ? `Regarding comment ${focused.number} (id ${focused.id}).` : "",
      focusedDocument
        ? `Currently viewing ${focusedDocument.name} (document id ${focusedDocument.id}${focusedSource ? `, source id ${focusedSource.id}` : ""}).`
        : "",
    ]
      .filter(Boolean)
      .join(" ");
    const withContext = context ? `${context} ${question}` : question;
    try {
      const result = await api<Answer>(`/orders/${w.order.id}/record/ask`, {
        question: withContext,
        history: messages.slice(-3).flatMap((m) => [
          { role: "user", content: m.question.slice(0, 6000) },
          { role: "assistant", content: m.result.answer.slice(0, 6000) },
        ]),
      });
      setMessages((prev) => [...prev, { question, result }]);
      setActiveQuestion("");
    } catch (e) {
      setError((e as Error).message);
      setPrompt(question);
      setActiveQuestion("");
    } finally {
      setAsking(false);
    }
  }
  async function check() {
    setChecking(true);
    setView("activity");
    try {
      await save("/coordination/run", {
        expected_version: w.version,
        actor: account.name || "Workspace user",
        reason: "Check order references, suggestions, and readiness",
      });
    } finally {
      setChecking(false);
    }
  }
  function visitComment(id: string) {
    openComment(id);
    if (window.matchMedia("(max-width: 1439px)").matches) setOpen(false);
  }
  function visitWork() {
    openWork();
    if (window.matchMedia("(max-width: 1439px)").matches) setOpen(false);
  }
  function visitSource(id: string) {
    openSource(id);
    if (drawer) setOpen(false);
  }
  return (
    <>
      {open && (
        <button
          className="record-agent-backdrop"
          aria-label="Close Rivet sidebar"
          onClick={() => setOpen(false)}
        />
      )}
      <aside
        id="record-agent-panel"
        role={drawer ? "dialog" : "complementary"}
        aria-modal={drawer && open ? true : undefined}
        ref={panel}
        className={"record-agent " + (open ? "is-open" : "is-closed")}
        aria-label="Rivet assistant"
        aria-hidden={!open}
      >
        <header className="record-agent-header">
          <div>
            <Mark small />
            <strong>Rivet</strong>
            <span>Order assistant</span>
          </div>
          <button
            className="icon-button"
            aria-label="Close Rivet assistant"
            onClick={() => setOpen(false)}
          >
            <SidebarSimple size={17} />
          </button>
        </header>
        <div className="record-agent-scope">
          <span>{w.order.number}</span>
          <span>Record v{w.version}</span>
        </div>
        <div className="record-agent-status">
          <span
            className={working ? "is-working" : failed.length ? "is-error" : ""}
          >
            {working ? (
              <CircleNotch size={15} />
            ) : failed.length ? (
              <WarningCircle size={15} />
            ) : (
              <Check size={15} />
            )}
            {status}
          </span>
          <button
            className="text-button"
            disabled={checking || recordBusy || queued.length > 0}
            onClick={() => void check()}
            title="Run source-link and readiness checks"
          >
            <ArrowsClockwise size={13} />
            Check record
          </button>
        </div>
        <nav className="record-agent-tabs" aria-label="Rivet sidebar views">
          <button
            aria-pressed={view === "conversation"}
            className={view === "conversation" ? "active" : ""}
            onClick={() => setView("conversation")}
          >
            Conversation
          </button>
          <button
            aria-pressed={view === "activity"}
            className={view === "activity" ? "active" : ""}
            onClick={() => setView("activity")}
          >
            Activity <span>{activity.length}</span>
          </button>
        </nav>
        <div className="record-agent-body">
          {view === "conversation" && (
            <>
              {focusedDocument && (
                <div className="record-agent-context">
                  <span>
                    <FileText size={13} />
                    Viewing source
                  </span>
                  <p>
                    {focusedDocument.name}
                    {focusedSource?.location.page
                      ? ` · p. ${focusedSource.location.page}`
                      : ""}
                  </p>
                  <small>Answers still use the full order record.</small>
                </div>
              )}
              {focused && (
                <div className="record-agent-context">
                  <span>
                    <ChatText size={13} />
                    Viewing comment {focused.number}
                  </span>
                  <p>{focused.text}</p>
                  <div>
                    {(focused.source_ids[0] || focused.document_id) && (
                      <button
                        className="text-button"
                        onClick={() =>
                          visitSource(
                            focused.source_ids[0] || focused.document_id,
                          )
                        }
                      >
                        <FileText size={12} />
                        Original source
                      </button>
                    )}
                    {links?.drawing_source_id && (
                      <button
                        className="text-button"
                        onClick={() => visitSource(links.drawing_source_id)}
                      >
                        <LinkSimple size={12} />
                        Linked drawing
                      </button>
                    )}
                  </div>
                </div>
              )}
              {!messages.length && (
                <div className="record-agent-welcome">
                  <h2>
                    {focused
                      ? "Ask about this comment"
                      : "Work through this order"}
                  </h2>
                  <p>
                    Find an answer, trace a reference, or review what needs
                    attention.
                  </p>
                  <div className="record-agent-prompts">
                    {suggestions.map((question) => (
                      <button
                        key={question}
                        onClick={() => {
                          setPrompt(question);
                          input.current?.focus();
                        }}
                      >
                        {question}
                        <ArrowUpRight size={13} />
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {pending.length > 0 && !messages.length && (
                <button className="record-agent-pending" onClick={visitWork}>
                  <ListChecks size={17} />
                  <span>
                    <strong>{pending.length} suggestions for review</strong>
                    <small>Drawing links and prepared follow-ups</small>
                  </span>
                  <ArrowRight size={14} />
                </button>
              )}
              {messages.map((message, index) => (
                <div className="record-agent-exchange" key={index}>
                  <div className="record-agent-question">
                    {message.question}
                  </div>
                  <article className="record-agent-answer">
                    <header>
                      <Mark small />
                      <strong>Rivet</strong>
                    </header>
                    <p>{message.result.answer}</p>
                    <div className="record-agent-citations">
                      {message.result.comment_ids.map((id) => (
                        <button
                          key={`comment:${id}`}
                          onClick={() => visitComment(id)}
                        >
                          <ChatText size={12} />
                          Comment {w.comments.find((c) => c.id === id)?.number}
                          <ArrowUpRight size={11} />
                        </button>
                      ))}
                      {message.result.source_ids.map((id) => (
                        <SourceButton
                          key={`source:${id}`}
                          w={w}
                          id={id}
                          open={visitSource}
                        />
                      ))}
                      {message.result.change_ids.map((id) => (
                        <button
                          key={`change:${id}`}
                          onClick={() => {
                            openChanges();
                            if (
                              window.matchMedia("(max-width: 1439px)").matches
                            )
                              setOpen(false);
                          }}
                        >
                          <LinkSimple size={12} />
                          {w.changes.find((c) => c.id === id)?.title ||
                            "Change record"}
                          <ArrowUpRight size={11} />
                        </button>
                      ))}
                    </div>
                    <small>
                      Record v{message.result.version}
                      {message.result.version !== w.version
                        ? " · Updated since this answer"
                        : ""}
                    </small>
                  </article>
                </div>
              ))}
              {asking && (
                <div className="record-agent-exchange">
                  <div className="record-agent-question">{activeQuestion}</div>
                  <div className="record-agent-working" role="status">
                    <CircleNotch size={15} />
                    Reading the order record and linked sources…
                  </div>
                </div>
              )}
              {error && <ErrorNote message={error} />}
              <div ref={end} />
            </>
          )}
          {view === "activity" && (
            <>
              {(queued.length > 0 || failed.length > 0) && (
                <section className="record-agent-files">
                  <h3>Documents</h3>
                  {[...queued, ...failed].map((document) => (
                    <div key={document.id}>
                      <FileText size={14} />
                      <span>
                        {document.name}
                        <small>
                          {document.state === "queued"
                            ? "Queued for document processing"
                            : "Processing failed · Open document details"}
                        </small>
                      </span>
                      {document.state === "queued" ? (
                        <CircleNotch className="is-working" size={14} />
                      ) : (
                        <button
                          className="text-button"
                          onClick={() => visitSource(document.id)}
                        >
                          <ArrowUpRight size={13} />
                        </button>
                      )}
                    </div>
                  ))}
                </section>
              )}
              {checking && (
                <div className="record-agent-working" role="status">
                  <CircleNotch size={15} />
                  Checking explicit references and open work…
                </div>
              )}
              {saveError && <ErrorNote message={saveError} />}
              {activity.length ? (
                <ol className="record-agent-timeline">
                  {activity.map((item) => (
                    <li
                      key={item.id}
                      className={item.undone_at ? "is-undone" : ""}
                    >
                      <span className="record-agent-event-icon">
                        {item.kind.includes("link") ? (
                          <LinkSimple size={12} />
                        ) : (
                          <Check size={12} />
                        )}
                      </span>
                      <div>
                        <h3>{item.title}</h3>
                        <div className="record-agent-event-meta">
                          <span title={item.actor}>
                            {item.actor === account.userId
                              ? account.name
                              : item.actor.startsWith("user_")
                                ? "Team member"
                                : item.actor || "Rivet"}
                          </span>
                          <time dateTime={item.at}>{when(item.at)}</time>
                        </div>
                        <details>
                          <summary>Reason & evidence</summary>
                          <p>{item.reason}</p>
                          <div className="record-agent-citations">
                            {item.comment_id && (
                              <button
                                onClick={() => visitComment(item.comment_id)}
                              >
                                <ChatText size={12} />
                                Comment{" "}
                                {
                                  w.comments.find(
                                    (c) => c.id === item.comment_id,
                                  )?.number
                                }
                                <ArrowUpRight size={11} />
                              </button>
                            )}
                            {item.source_ids.map((id) => (
                              <SourceButton
                                key={id}
                                w={w}
                                id={id}
                                open={visitSource}
                              />
                            ))}
                          </div>
                        </details>
                        {item.undone_at ? (
                          <span className="record-agent-undone">
                            Undone {when(item.undone_at)}
                          </span>
                        ) : (
                          item.undoable && (
                            <button
                              className="text-button record-agent-undo"
                              disabled={recordBusy}
                              onClick={() =>
                                void save(
                                  `/coordination/activity/${item.id}/undo`,
                                  {
                                    expected_version: w.version,
                                    actor: account.name || "Workspace user",
                                    reason: `Undo: ${item.title}`,
                                  },
                                )
                              }
                            >
                              <ArrowCounterClockwise size={12} />
                              Undo
                            </button>
                          )
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <div className="record-agent-welcome">
                  <h2>No activity yet</h2>
                  <p>
                    Rivet records source links, assignments, and reviewed
                    suggestions here. Add a document or check the record to
                    begin.
                  </p>
                </div>
              )}
              <p className="record-agent-log-note">
                Recorded actions from this order. Drafts stay unsent until a
                person sends them.
              </p>
            </>
          )}
        </div>
        {view === "conversation" && (
          <footer className="record-agent-composer">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void ask(prompt);
              }}
            >
              <textarea
                ref={input}
                aria-label="Ask Rivet about this order"
                placeholder={
                  focused
                    ? `Ask about comment ${focused.number}…`
                    : "Ask about this order…"
                }
                rows={3}
                maxLength={5000}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void ask(prompt);
                  }
                }}
              />
              <div>
                <span>
                  <FileText size={12} />
                  Order context attached
                </span>
                <button
                  className="record-agent-send"
                  type="submit"
                  disabled={asking || !prompt.trim()}
                  aria-label="Send question"
                >
                  {asking ? (
                    <CircleNotch size={16} />
                  ) : (
                    <ArrowRight size={17} />
                  )}
                </button>
              </div>
            </form>
            <p>Answers use this record. Review proposals in the work queue.</p>
          </footer>
        )}
        {view === "activity" && (
          <footer className="record-agent-activity-footer">
            <button className="secondary" onClick={visitWork}>
              <ListChecks size={14} />
              Review work queue <ArrowRight size={13} />
            </button>
            <span>
              {w.coordination?.last_run_at
                ? `Last check ${when(w.coordination.last_run_at)}`
                : "No check recorded yet"}
            </span>
          </footer>
        )}
      </aside>
    </>
  );
}

function SourceButton({
  w,
  id,
  open,
}: {
  w: RecordView;
  id: string;
  open: (id: string) => void;
}) {
  const source = w.sources.find((s) => s.id === id);
  const document = w.documents.find((d) => d.id === source?.document_id);
  return (
    <button onClick={() => open(id)} title={document?.name}>
      <FileText size={12} />
      <span>
        {document?.name || "Source"}
        {source?.location.page
          ? ` · p. ${source.location.page}`
          : source?.location.line
            ? ` · line ${source.location.line}`
            : ""}
      </span>
      <ArrowUpRight size={11} />
    </button>
  );
}
