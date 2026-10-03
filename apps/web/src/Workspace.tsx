import { authorizationHeaders } from "./api";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  type ColumnDef,
} from "@tanstack/react-table";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Plus,
  Check,
  Checks,
  X,
  FileText,
  UploadSimple,
  DownloadSimple,
  GitDiff,
  Clock,
  LinkSimple,
  ChatCircleText,
  CaretDown,
  SidebarSimple,
  PaperPlaneRight,
  WarningCircle,
  CheckCircle,
  CaretRight,
  ArrowsOutSimple,
  SlidersHorizontal,
  MagnifyingGlass,
  Eye,
  EyeSlash,
  Info,
  Files,
  NotePencil,
  Trash,
  ArrowCounterClockwise,
  Copy,
  ShieldCheck,
  SpinnerGap,
  Stack,
} from "@phosphor-icons/react";
import {
  api,
  usd,
  dateLabel,
  when,
  type Workspace,
  type Quote,
  type Line,
  type Doc,
  type Proposal,
  type Operation,
} from "./api";
import { Mark, Status, Modal, Empty, ErrorNote, Busy } from "./ui";
import { BidCoordinator } from "./BidCoordinator";
import "./workspace-refinement.css";

function projectLocation() {
  const query = location.hash.split("?")[1] ?? "";
  const params = new URLSearchParams(query);
  const requested = params.get("tab") ?? query.split("&")[0];
  return {
    tab: ["coordination", "quote", "sources", "changes", "history"].includes(
      requested,
    )
      ? requested
      : "coordination",
    issue: params.get("issue"),
  };
}

type Props = {
  id: string;
  notify: (s: string) => void;
  assistantReady: boolean;
  onBack: () => void;
};
export function QuoteWorkspace({ id, notify, assistantReady, onBack }: Props) {
  const qc = useQueryClient();
  const {
    data: w,
    error,
    isLoading,
  } = useQuery({
    queryKey: ["workspace", id],
    queryFn: () => api<Workspace>("/projects/" + id),
    refetchInterval: 2500,
  });
  const [tab, setTabState] = useState(() => projectLocation().tab);
  const [issueId, setIssueId] = useState<string | null>(
    () => projectLocation().issue,
  );
  const [reviewRequest, setReviewRequest] = useState(0);
  const setTab = useCallback(
    (nextTab: string) => {
      setTabState(nextTab);
      setIssueId(null);
      if (nextTab !== "coordination") setReviewRequest(0);
      location.hash = `project/${id}?${nextTab}`;
    },
    [id],
  );
  const selectIssue = useCallback(
    (nextIssue: string | null) => {
      setIssueId(nextIssue);
      setTabState("coordination");
      location.hash = `project/${id}?coordination${nextIssue ? `&issue=${encodeURIComponent(nextIssue)}` : ""}`;
    },
    [id],
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [showCosts, setShowCosts] = useState(false);
  const [panel, setPanel] = useState<"evidence" | "assistant" | null>(null);
  const [showChecks, setShowChecks] = useState(false);
  const inspectorRef = useRef<HTMLElement>(null);
  const inspectorOpen = panel !== null;
  const [modal, setModal] = useState<string | null>(null);
  const [viewer, setViewer] = useState<{ doc: Doc; sourceId?: string } | null>(
    null,
  );
  const [mapping, setMapping] = useState<Doc | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const syncRoute = () => {
      const route = projectLocation();
      setTabState(route.tab);
      setIssueId(route.issue);
      if (route.tab !== "coordination") setReviewRequest(0);
    };
    syncRoute();
    window.addEventListener("hashchange", syncRoute);
    setSelected(null);
    setPanel(null);
    setShowChecks(false);
    setChecked([]);
    setActionError("");
    setReviewRequest(0);
    return () => window.removeEventListener("hashchange", syncRoute);
  }, [id]);
  useEffect(() => {
    if (!inspectorOpen) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() => {
      inspectorRef.current
        ?.querySelector<HTMLButtonElement>(".inspector-close")
        ?.focus({ preventScroll: true });
    });
    return () => {
      cancelAnimationFrame(frame);
      if (previousFocus?.isConnected)
        previousFocus.focus({ preventScroll: true });
    };
  }, [inspectorOpen]);
  useEffect(() => {
    if (!panel || modal || viewer || mapping) return;
    const closePanel = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setPanel(null);
      }
    };
    window.addEventListener("keydown", closePanel);
    return () => window.removeEventListener("keydown", closePanel);
  }, [panel, modal, viewer, mapping]);
  const refresh = useCallback(async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["workspace", id] }),
      qc.invalidateQueries({ queryKey: ["projects"] }),
      qc.invalidateQueries({ queryKey: ["catalog"] }),
      qc.invalidateQueries({ queryKey: ["clarifications", id] }),
      qc.invalidateQueries({ queryKey: ["work-queue"] }),
    ]);
  }, [id, qc]);
  const command = async (
    operations: Operation[],
    message = "Changes saved",
  ) => {
    if (!w) return;
    setBusy(true);
    setActionError("");
    try {
      await api("/quotes/" + w.quote.id + "/commands", {
        expected_version: w.quote.version,
        expected_input_revision: w.quote.input_revision,
        operations,
      });
      await refresh();
      notify(message);
    } catch (e) {
      setActionError((e as Error).message);
      throw e;
    } finally {
      setBusy(false);
    }
  };
  const safeCommand = (ops: Operation[], message?: string) => {
    void command(ops, message).catch(() => {});
  };
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setModal("command");
      }
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, []);
  if (isLoading)
    return (
      <main className="page">
        <Busy text="Opening project…" />
      </main>
    );
  if (error || !w)
    return (
      <main className="page">
        <ErrorNote
          message={(error as Error)?.message ?? "Project not found."}
        />
        <button onClick={onBack}>Back to projects</button>
      </main>
    );
  const q = w.quote,
    line = q.lines.find((l) => l.id === selected) ?? null;
  const pending = w.proposals.filter((p) => p.status === "pending");
  const reviewedCount = q.lines.filter((l) => l.review === "approved").length;
  const selectLine = (lineId: string) => {
    setSelected(lineId);
    setPanel("evidence");
  };
  const openSource = (sourceId: string) => {
    const sp = w.sources.find((s: any) => s.id === sourceId) as any;
    const d = w.documents.find((d) => d.id === sp?.document_id);
    if (d) setViewer({ doc: d, sourceId });
  };
  const accept = async (pr: Proposal, reject = false) => {
    setBusy(true);
    setActionError("");
    try {
      await api("/proposals/" + pr.id + (reject ? "/reject" : "/accept"), {
        expected_version: q.version,
        expected_input_revision: q.input_revision,
        reason: reject ? "Dismissed by estimator" : "",
      });
      await refresh();
      notify(
        reject
          ? "Proposal dismissed. Quote unchanged."
          : "Changes applied in a new draft revision.",
      );
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="workspace-page workspace-refined">
      <div className="project-heading">
        <div>
          <button className="back-link" onClick={onBack}>
            <ArrowLeft size={14} />
            Work queue
          </button>
          <div className="title-line">
            <h1>{w.project.title}</h1>
            {w.project.synthetic && (
              <span className="micro-tag">Sample project</span>
            )}
          </div>
          <div className="project-meta">
            <span>{w.project.customer}</span>
            <i />
            <span>{q.number}</span>
            <i />
            <span>
              {w.project.due_date
                ? `Due ${dateLabel(w.project.due_date)}`
                : "No deadline"}
            </span>
          </div>
        </div>
        <div className="workspace-heading-actions">
          <button
            className="secondary"
            onClick={() => {
              setTab("coordination");
              setReviewRequest((n) => n + 1);
            }}
          >
            <GitDiff size={17} />
            Review change
          </button>
          <button className="primary" onClick={() => setModal("export")}>
            <DownloadSimple size={17} />
            Review & export
          </button>
        </div>
      </div>
      <nav className="workspace-tabs" aria-label="Project sections">
        <div>
          {[
            ["coordination", "Work", null],
            ["quote", "Quote", q.lines.length],
            ["sources", "Sources", w.documents.length],
            ["changes", "Changes", pending.length],
            ["history", "History", w.history.length],
          ].map(([name, label, count]) => (
            <button
              key={name}
              className={tab === name ? "active" : ""}
              aria-current={tab === name ? "page" : undefined}
              onClick={() => setTab(String(name))}
            >
              {label}
              {count !== null && (
                <span
                  className={
                    name === "changes" && Number(count) > 0
                      ? "violet-count"
                      : ""
                  }
                >
                  {count}
                </span>
              )}
            </button>
          ))}
        </div>
        <span className="version-pill">
          Revision {q.version}
          <span className="divider" />
          <Status status={q.status} />
        </span>
      </nav>
      {actionError && (
        <div className="workspace-error">
          <ErrorNote message={actionError} />
          <button
            className="icon-button"
            aria-label="Dismiss error"
            onClick={() => setActionError("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {tab === "coordination" && (
        <BidCoordinator
          w={w}
          configured={assistantReady}
          issueId={issueId}
          reviewRequest={reviewRequest}
          onSelectIssue={selectIssue}
          onUpload={() => setModal("upload")}
          onSources={() => setTab("sources")}
          onQuote={() => setTab("quote")}
          onChanges={() => setTab("changes")}
          onReviewInputs={() => setModal("reconcile")}
          openSource={openSource}
          refresh={refresh}
          notify={notify}
        />
      )}
      {tab === "quote" && (
        <>
          <div className="quote-summary">
            <div className="quote-total">
              <span className="stat-label">
                Quote total <small>USD</small>
              </span>
              <strong>{usd(q.total, 2)}</strong>
              {!q.total_complete && (
                <small className="warning-text">
                  Partial total · missing prices
                </small>
              )}
            </div>
            <div className="quote-margin">
              <span className="stat-label">Gross margin</span>
              <strong>
                {q.margin ?? "—"}
                {q.margin !== null && <small>%</small>}
              </strong>
            </div>
            <div className="quote-review-status">
              <span className="stat-label">Line review</span>
              <span className="review-count">
                <strong>{reviewedCount}</strong> of {q.lines.length} reviewed
              </span>
              {q.checks.length > 0 ? (
                <button
                  className="review-check-toggle"
                  aria-expanded={showChecks}
                  aria-controls="quote-review-checks"
                  onClick={() => setShowChecks(!showChecks)}
                >
                  {q.checks.length} {q.checks.length === 1 ? "check" : "checks"}{" "}
                  to resolve
                  <CaretDown size={14} />
                </button>
              ) : (
                <span className="review-ready">
                  <CheckCircle size={14} />{" "}
                  {w.quote.status === "approved"
                    ? "Revision approved"
                    : "Ready for approval"}
                </span>
              )}
            </div>
          </div>
          {q.checks.length > 0 && showChecks && (
            <section id="quote-review-checks" className="readiness-box">
              <div>
                <WarningCircle size={18} />
                <h3>Resolve before approval</h3>
              </div>
              {q.checks.slice(0, 4).map((c, i) => (
                <button
                  key={i}
                  onClick={() => {
                    if (c.code === "clarification") {
                      setPanel(null);
                      setTab("coordination");
                    } else if (c.line_id) selectLine(c.line_id);
                    else if (c.code === "reconcile") setModal("reconcile");
                    else setTab("sources");
                  }}
                >
                  <span>{c.message}</span>
                  <CaretRight size={15} />
                </button>
              ))}
              {q.checks.length > 4 && (
                <button
                  className="text-button"
                  onClick={() => setModal("export")}
                >
                  View all {q.checks.length} checks <ArrowRight size={15} />
                </button>
              )}
            </section>
          )}
          {pending.length > 0 && (
            <button
              className="inline-change-banner"
              onClick={() => setTab("changes")}
            >
              <GitDiff size={17} />
              <strong>{pending[0].title}</strong>
              <span>
                {pending.length === 1
                  ? "Review changes"
                  : `${pending.length} proposals to review`}
              </span>
              <ArrowRight size={16} />
            </button>
          )}
          <div className={"quote-layout" + (panel ? " has-inspector" : "")}>
            <section className="quote-main">
              <div className="quote-toolbar">
                <label className="compact-search">
                  <MagnifyingGlass size={16} />
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Find equipment…"
                    aria-label="Find equipment"
                  />
                </label>
                <div>
                  <button
                    className="quiet-button"
                    aria-pressed={showCosts}
                    onClick={() => setShowCosts(!showCosts)}
                  >
                    {showCosts ? <EyeSlash size={16} /> : <Eye size={16} />}
                    {showCosts ? "Hide costs" : "Show costs"}
                  </button>
                  <button
                    className="quiet-button"
                    onClick={() => setModal("pricing")}
                    disabled={!q.lines.length}
                  >
                    <SlidersHorizontal size={15} />
                    Pricing
                  </button>
                  <button
                    className="quiet-button"
                    onClick={() => setModal("line")}
                  >
                    <Plus size={16} />
                    Add line
                  </button>
                  {selected && panel !== "evidence" && (
                    <button
                      className="quiet-button"
                      onClick={() => setPanel("evidence")}
                    >
                      <SidebarSimple size={16} /> Details
                    </button>
                  )}
                </div>
              </div>
              {checked.length > 0 && (
                <div className="selection-bar">
                  <span>{checked.length} selected</span>
                  <button
                    onClick={() =>
                      safeCommand(
                        checked.map((line_id) => ({
                          type: "review_line",
                          line_id,
                          reason: "Reviewed selected lines",
                        })),
                        "Selected lines reviewed",
                      )
                    }
                    disabled={busy}
                  >
                    <Check size={14} />
                    Mark reviewed
                  </button>
                  <button onClick={() => setModal("command")}>
                    <ChatCircleText size={14} />
                    Ask Rivet
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Clear selected lines"
                    onClick={() => setChecked([])}
                  >
                    <X size={14} />
                  </button>
                </div>
              )}
              <QuoteGrid
                lines={q.lines.filter((l) =>
                  (l.tag + " " + l.description + " " + l.model)
                    .toLowerCase()
                    .includes(query.toLowerCase()),
                )}
                selected={selected}
                onSelect={selectLine}
                checked={checked}
                setChecked={setChecked}
                showCosts={showCosts}
                busy={busy}
                edit={(l) => {
                  setSelected(l.id);
                  setModal("edit");
                }}
                onQuantity={(l, value) =>
                  command([
                    {
                      type: "set_quantity",
                      line_id: l.id,
                      value,
                      expected_before: l.quantity,
                      reason: "Manual quantity edit",
                    },
                  ])
                }
              />
              {q.lines.length > 0 &&
                !q.lines.some((l) =>
                  (l.tag + " " + l.description + " " + l.model)
                    .toLowerCase()
                    .includes(query.toLowerCase()),
                ) && (
                  <div className="quote-no-results">
                    <p>No equipment matches “{query}”.</p>
                    <button
                      className="text-button"
                      onClick={() => setQuery("")}
                    >
                      Clear search
                    </button>
                  </div>
                )}
              {!q.lines.length && (
                <Empty
                  icon={<Stack size={32} />}
                  title="Add equipment to this quote"
                  action={
                    <button
                      className="primary"
                      onClick={() => {
                        setTab("sources");
                        setModal("upload");
                      }}
                    >
                      <UploadSimple size={16} />
                      Upload a document
                    </button>
                  }
                >
                  Upload a schedule to create your first equipment lines, or add
                  a line manually.
                </Empty>
              )}
              <div className="grid-footer">
                <span>
                  {q.lines.length} equipment lines
                  <span className="footer-dot">·</span>Prices in USD per stated
                  unit
                </span>
                <div>
                  <span>Subtotal</span>
                  <strong>{usd(q.total, 2)}</strong>
                </div>
              </div>
              <div className="grid-help">
                <span>
                  Click a line for source evidence. Click a quantity or price to
                  edit.
                </span>
                <span>
                  <kbd>↑</kbd>
                  <kbd>↓</kbd> Navigate
                </span>
              </div>
              <details className="terms-section">
                <summary>
                  Terms & exclusions <CaretDown size={15} />
                </summary>
                <div>
                  <p>{q.terms || "No terms added yet."}</p>
                  <button
                    className="text-button"
                    onClick={() => setModal("terms")}
                  >
                    <NotePencil size={15} /> Edit terms
                  </button>
                </div>
              </details>
            </section>
            {panel && (
              <aside
                ref={inspectorRef}
                className="inspector"
                aria-label={
                  panel === "evidence"
                    ? "Equipment details"
                    : "Project assistant"
                }
              >
                <div className="inspector-tabs">
                  <button
                    className={panel === "evidence" ? "active" : ""}
                    onClick={() => setPanel("evidence")}
                  >
                    <LinkSimple size={15} />
                    Details
                  </button>
                  <button
                    className={panel === "assistant" ? "active" : ""}
                    onClick={() => setPanel("assistant")}
                  >
                    <ChatCircleText size={15} />
                    Assistant
                  </button>
                  <button
                    className="inspector-close icon-button"
                    aria-label="Close details panel"
                    onClick={() => setPanel(null)}
                  >
                    <X size={18} />
                  </button>
                </div>
                {panel === "evidence" ? (
                  <Evidence
                    w={w}
                    line={line}
                    openSource={openSource}
                    selectProduct={() => setModal("catalog")}
                    edit={() => setModal("edit")}
                    review={() =>
                      line &&
                      safeCommand(
                        [
                          {
                            type: "review_line",
                            line_id: line.id,
                            reason: "Reviewed line details and sources",
                          },
                        ],
                        "Line reviewed",
                      )
                    }
                    busy={busy}
                    goSources={() => setTab("sources")}
                  />
                ) : (
                  <Assistant
                    w={w}
                    openSource={openSource}
                    configured={assistantReady}
                    refresh={refresh}
                    notify={notify}
                    selected={
                      checked.length ? checked : selected ? [selected] : []
                    }
                  />
                )}
              </aside>
            )}
          </div>
        </>
      )}
      {tab === "sources" && (
        <Sources
          w={w}
          onUpload={() => setModal("upload")}
          onPaste={() => setModal("paste")}
          onView={(d) => setViewer({ doc: d })}
          onMap={setMapping}
          startAI={() => {
            setTab("quote");
            setPanel("assistant");
          }}
        />
      )}
      {tab === "changes" && (
        <div className="changes-page">
          <div className="section-heading">
            <div>
              <h2>Proposed changes</h2>
              <p>
                Accepting a proposal creates a new draft. Every change stays in
                the history.
              </p>
            </div>
            <button className="secondary" onClick={() => setModal("upload")}>
              <Plus size={16} />
              Add an addendum
            </button>
          </div>
          {w.proposals.length === 0 ? (
            <Empty
              icon={<GitDiff size={30} />}
              title="Nothing waiting for review"
            >
              Upload an addendum or ask Rivet to propose changes.
            </Empty>
          ) : (
            w.proposals.map((pr) => (
              <ChangeCard
                key={pr.id}
                pr={pr}
                w={w}
                accept={() => accept(pr)}
                reject={() => accept(pr, true)}
                openSource={openSource}
                busy={busy}
                reanalyze={() => {
                  setTab("sources");
                  notify(
                    "Open the source and confirm its mapping again, or ask the assistant to reanalyze it.",
                  );
                }}
              />
            ))
          )}
        </div>
      )}
      {tab === "history" && <History w={w} refresh={refresh} notify={notify} />}
      {modal === "catalog" && line && (
        <SelectProduct
          onClose={() => setModal(null)}
          onSave={async (id) => {
            await command([
              {
                type: "select_catalog_item",
                line_id: line.id,
                value: id,
                reason: "Estimator selected catalog equipment",
              },
            ]);
            setModal(null);
          }}
        />
      )}
      {modal === "line" && (
        <LineModal
          onClose={() => setModal(null)}
          onSave={async (value) => {
            await command([
              {
                type: "add_line",
                line: value,
                reason: "Manual equipment entry",
              },
            ]);
            setModal(null);
          }}
        />
      )}
      {modal === "edit" && line && (
        <LineModal
          line={line}
          onClose={() => setModal(null)}
          onSave={async (value) => {
            const ops: Operation[] = [];
            for (const [field, type] of [
              ["quantity", "set_quantity"],
              ["description", "update_description"],
              ["cost", "set_cost"],
              ["price", "override_selling_price"],
              ["lead_time", "set_lead_time"],
            ] as const) {
              if (String(value[field] ?? "") !== String(line[field] ?? ""))
                ops.push({
                  type,
                  line_id: line.id,
                  value: String(value[field]),
                  expected_before: String(line[field] ?? ""),
                  reason: value.reason ?? "Estimator manual edit",
                });
            }
            if (ops.length) await command(ops);
            setModal(null);
          }}
        />
      )}
      {modal === "pricing" && (
        <Pricing
          count={checked.length || q.lines.length}
          onClose={() => setModal(null)}
          onSave={async (basis, percent) => {
            await command(
              (checked.length ? checked : q.lines.map((l) => l.id)).map(
                (line_id) => ({
                  type: basis === "margin" ? "set_gross_margin" : "set_markup",
                  line_id,
                  value: percent,
                  reason: "Estimator pricing policy",
                }),
              ),
              `${basis === "margin" ? "Gross margin" : "Markup"} applied`,
            );
            setModal(null);
          }}
        />
      )}
      {modal === "upload" && (
        <Upload
          id={id}
          initialKind={
            tab === "changes" || tab === "coordination"
              ? "addendum"
              : "schedule"
          }
          onClose={() => setModal(null)}
          onDone={async () => {
            setModal(null);
            if (tab !== "coordination") setTab("sources");
            await refresh();
            notify("Document uploaded. Rivet is reading the source.");
          }}
        />
      )}
      {modal === "paste" && (
        <Paste
          id={id}
          onClose={() => setModal(null)}
          onDone={async () => {
            setModal(null);
            await refresh();
            notify("Request saved as an immutable source.");
          }}
        />
      )}
      {modal === "terms" && (
        <TextModal
          title="Terms & exclusions"
          initial={q.terms}
          label="Customer-facing terms"
          onClose={() => setModal(null)}
          onSave={async (value) => {
            await command([
              { type: "set_terms", value, reason: "Updated customer terms" },
            ]);
            setModal(null);
          }}
        />
      )}
      {modal === "reconcile" && (
        <TextModal
          title="Reconcile the sources"
          initial=""
          label="How have you accounted for all current source documents?"
          hint="Confirm document precedence, new requirements, and any scope gaps before continuing. This records your review for input revision "
          onClose={() => setModal(null)}
          onSave={async (reason) => {
            await command(
              [{ type: "reconcile_inputs", reason }],
              "Source revision reconciled",
            );
            setModal(null);
          }}
        />
      )}
      {modal === "command" && (
        <Modal
          title="Ask Rivet"
          eyebrow={
            checked.length
              ? `${checked.length} lines selected`
              : "Project assistant"
          }
          onClose={() => setModal(null)}
        >
          <Assistant
            w={w}
            openSource={(sourceId) => {
              setModal(null);
              openSource(sourceId);
            }}
            configured={assistantReady}
            refresh={refresh}
            notify={notify}
            selected={checked.length ? checked : selected ? [selected] : []}
          />
        </Modal>
      )}
      {modal === "export" && (
        <ExportReview
          w={w}
          onClose={() => setModal(null)}
          refresh={refresh}
          notify={notify}
          onFix={(c) => {
            setModal(null);
            if (c.code === "clarification") {
              setPanel(null);
              setTab("coordination");
            } else if (c.line_id) {
              setTab("quote");
              selectLine(c.line_id);
            } else if (c.code === "reconcile") setModal("reconcile");
            else setTab("sources");
          }}
        />
      )}
      {viewer && (
        <DocumentViewer
          doc={viewer.doc}
          sources={w.sources as any[]}
          sourceId={viewer.sourceId}
          onClose={() => setViewer(null)}
          onMap={() => {
            setMapping(viewer.doc);
            setViewer(null);
          }}
        />
      )}
      {mapping && (
        <MappingModal
          doc={mapping}
          inputRevision={q.input_revision}
          onClose={() => setMapping(null)}
          onDone={async () => {
            setMapping(null);
            await refresh();
            setTab("changes");
            notify("Mapping confirmed. Review the sourced proposal.");
          }}
        />
      )}
    </main>
  );
}

function EditableQty({
  line,
  onSave,
  disabled,
}: {
  line: Line;
  onSave: (l: Line, v: string) => Promise<void>;
  disabled: boolean;
}) {
  const [editing, setEditing] = useState(false),
    [value, setValue] = useState(line.quantity);
  useEffect(() => setValue(line.quantity), [line.quantity]);
  const save = async () => {
    if (value === line.quantity) {
      setEditing(false);
      return;
    }
    try {
      await onSave(line, value);
      setEditing(false);
    } catch {
      setValue(line.quantity);
      setEditing(false);
    }
  };
  return editing ? (
    <input
      className="cell-input"
      aria-label={"Quantity for " + line.tag}
      autoFocus
      value={value}
      inputMode="decimal"
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => void save()}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          setValue(line.quantity);
          setEditing(false);
        } else if (e.key === "Enter") {
          e.preventDefault();
          void save();
        }
      }}
    />
  ) : (
    <button
      disabled={disabled}
      className="quantity-cell"
      aria-label={"Edit quantity for " + line.tag}
      onClick={() => setEditing(true)}
    >
      {line.quantity}
      <small>{line.unit}</small>
    </button>
  );
}
function QuoteGrid({
  lines,
  selected,
  onSelect,
  checked,
  setChecked,
  showCosts,
  busy,
  edit,
  onQuantity,
}: {
  lines: Line[];
  selected: string | null;
  onSelect: (s: string) => void;
  checked: string[];
  setChecked: (s: string[]) => void;
  showCosts: boolean;
  busy: boolean;
  edit: (l: Line) => void;
  onQuantity: (l: Line, v: string) => Promise<void>;
}) {
  const columns = useMemo<ColumnDef<Line>[]>(
    () => [
      {
        id: "select",
        header: () => (
          <input
            type="checkbox"
            aria-label="Select all equipment"
            checked={
              lines.length > 0 && lines.every((l) => checked.includes(l.id))
            }
            onChange={(e) =>
              setChecked(e.target.checked ? lines.map((l) => l.id) : [])
            }
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            aria-label={"Select " + row.original.tag}
            checked={checked.includes(row.original.id)}
            onChange={(e) =>
              setChecked(
                e.target.checked
                  ? [...checked, row.original.id]
                  : checked.filter((id) => id !== row.original.id),
              )
            }
          />
        ),
      },
      {
        accessorKey: "description",
        header: "Equipment",
        cell: ({ row }) => (
          <button
            className="equipment-cell"
            onClick={() => onSelect(row.original.id)}
          >
            <span>{row.original.tag}</span>
            <strong>{row.original.description}</strong>
            <small>{row.original.model || "Product selection needed"}</small>
          </button>
        ),
      },
      {
        accessorKey: "quantity",
        header: "Quantity",
        cell: ({ row }) => (
          <EditableQty
            line={row.original}
            onSave={onQuantity}
            disabled={busy}
          />
        ),
      },
      ...(showCosts
        ? ([
            {
              accessorKey: "cost",
              header: "Unit cost",
              cell: ({ row }: any) => (
                <button
                  className="money-cell"
                  onClick={() => edit(row.original)}
                >
                  {usd(row.original.cost)}
                </button>
              ),
            },
          ] as ColumnDef<Line>[])
        : []),
      {
        accessorKey: "price",
        header: "Unit price",
        cell: ({ row }) => (
          <button className="money-cell" onClick={() => edit(row.original)}>
            {usd(row.original.price)}
          </button>
        ),
      },
      {
        accessorKey: "extended",
        header: "Amount",
        cell: ({ row }) => (
          <span className="amount-cell">{usd(row.original.extended)}</span>
        ),
      },
      {
        accessorKey: "review",
        header: "Review",
        cell: ({ row }) => (
          <button
            className="review-cell"
            onClick={() => onSelect(row.original.id)}
          >
            {row.original.review === "approved" ? (
              <CheckCircle size={16} />
            ) : (
              <Clock size={16} className="review-pending" />
            )}
            <span>
              {row.original.review === "approved" ? "Reviewed" : "To review"}
            </span>
          </button>
        ),
      },
    ],
    [lines, checked, showCosts, busy],
  );
  const table = useReactTable({
    data: lines,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <div className="grid-scroll">
      <table
        className="quote-grid"
        aria-label="Editable equipment quote"
        onKeyDown={(e) => {
          if (
            !["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(
              e.key,
            ) ||
            (e.target as HTMLElement).tagName === "INPUT"
          )
            return;
          const cell = (e.target as HTMLElement).closest("td");
          const row = cell?.parentElement;
          if (!row || !cell) return;
          const idx = [...row.children].indexOf(cell);
          let target: Element | null = null;
          if (e.key === "ArrowDown" || e.key === "ArrowUp")
            target =
              (e.key === "ArrowDown"
                ? row.nextElementSibling
                : row.previousElementSibling
              )?.children[idx] ?? null;
          else
            target =
              e.key === "ArrowRight"
                ? cell.nextElementSibling
                : cell.previousElementSibling;
          const focus = target?.querySelector<HTMLElement>("button,input");
          if (focus) {
            e.preventDefault();
            focus.focus();
          }
        }}
      >
        <thead>
          {table.getHeaderGroups().map((g) => (
            <tr key={g.id}>
              {g.headers.map((h) => (
                <th key={h.id}>
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              className={selected === row.original.id ? "selected" : ""}
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Evidence({
  w,
  line,
  openSource,
  selectProduct,
  edit,
  review,
  busy,
  goSources,
}: {
  w: Workspace;
  line: Line | null;
  openSource: (id: string) => void;
  selectProduct: () => void;
  edit: () => void;
  review: () => void;
  busy: boolean;
  goSources: () => void;
}) {
  if (!line)
    return (
      <div className="evidence-intro">
        <h3>Select a line</h3>
        <p>
          Choose equipment in the quote to review its sources, pricing and
          delivery.
        </p>
        <button className="text-button" onClick={goSources}>
          Browse documents <ArrowRight size={15} />
        </button>
      </div>
    );
  const meta = line.meta as any,
    sourceIds = (meta.source_ids ?? []) as string[],
    problems = w.quote.checks.filter((c) => c.line_id === line.id);
  return (
    <div className="line-evidence">
      <div className="line-evidence-title">
        <span className="eyebrow">{line.tag}</span>
        <Status status={line.review} />
      </div>
      <h3>{line.description}</h3>
      <code>{line.model || "No product selected"}</code>
      <div className="origin-row">
        <span className="label">Value origin</span>
        <span>{meta.origin ?? "Human entered"}</span>
      </div>
      <div className="evidence-label">
        <span>Source evidence</span>
        <span>{sourceIds.length}</span>
      </div>
      {sourceIds.map((id) => {
        const sp = w.sources.find((s: any) => s.id === id) as any;
        const doc = w.documents.find((d) => d.id === sp?.document_id);
        return sp ? (
          <button
            key={id}
            className="source-card"
            onClick={() => openSource(id)}
          >
            <div>
              <FileText size={15} />
              <span>{doc?.name ?? "Source"}</span>
              <ArrowUpRight size={14} />
            </div>
            <blockquote>“{sp.text}”</blockquote>
            <span className="source-location">
              {sp.location.page
                ? "Page " + sp.location.page
                : sp.location.cells
                  ? "Cells " + sp.location.cells
                  : "Line " + (sp.location.line ?? "—")}
            </span>
          </button>
        ) : null;
      })}
      {!sourceIds.length && (
        <div className="no-evidence">
          <Info size={17} />
          <p>
            Human-entered value. Attach source evidence through the assistant or
            record an assumption during your review.
          </p>
        </div>
      )}
      <div className="evidence-label">Commercial details</div>
      <dl className="line-details">
        <div>
          <dt>Supplier cost</dt>
          <dd>{usd(line.cost, 2)}</dd>
        </div>
        <div>
          <dt>Selling price</dt>
          <dd>{usd(line.price, 2)}</dd>
        </div>
        <div>
          <dt>Price basis</dt>
          <dd>Per {line.unit}</dd>
        </div>
        <div>
          <dt>Delivery</dt>
          <dd>{line.lead_time}</dd>
        </div>
      </dl>
      {meta.calculation && (
        <div className="calculation">
          <span>Calculated price</span>
          <p>
            {meta.calculation.type === "set_gross_margin"
              ? "Cost ÷ (1 − margin)"
              : "Cost × (1 + markup)"}
          </p>
          <code>
            {usd(meta.calculation.cost)} · {meta.calculation.percent}%
          </code>
        </div>
      )}
      {problems
        .filter((p) => p.code !== "review")
        .map((p, i) => (
          <div key={i} className="line-problem">
            <WarningCircle size={15} />
            <span>{p.message}</span>
          </div>
        ))}
      <div className="line-actions">
        <button className="secondary" onClick={selectProduct}>
          <Stack size={15} />
          Select from catalog
        </button>
        <button className="secondary" onClick={edit}>
          <NotePencil size={15} />
          Edit details
        </button>
        <button
          className="primary"
          disabled={busy || line.review === "approved"}
          onClick={review}
        >
          <Check size={15} />
          {line.review === "approved" ? "Reviewed" : "Mark reviewed"}
        </button>
      </div>
    </div>
  );
}
function Sources({
  w,
  onUpload,
  onPaste,
  onView,
  onMap,
  startAI,
}: {
  w: Workspace;
  onUpload: () => void;
  onPaste: () => void;
  onView: (d: Doc) => void;
  onMap: (d: Doc) => void;
  startAI: () => void;
}) {
  return (
    <div className="sources-page">
      <div className="section-heading">
        <div>
          <h2>Project sources</h2>
          <p>
            Original documents stay intact. New uploads trigger a fresh review.
          </p>
        </div>
        <div>
          <button className="secondary" onClick={onPaste}>
            <NotePencil size={16} />
            Paste request
          </button>
          <button className="primary" onClick={onUpload}>
            <UploadSimple size={17} />
            Upload document
          </button>
        </div>
      </div>
      <div className="source-list">
        {w.documents.map((d) => (
          <article key={d.id}>
            <div className="file-icon">
              <FileText size={23} />
            </div>
            <button className="source-name" onClick={() => onView(d)}>
              <h3>{d.name}</h3>
              <p>
                {d.kind.charAt(0).toUpperCase() + d.kind.slice(1)}
                <span>·</span>
                {d.coverage.length} section{d.coverage.length !== 1 ? "s" : ""}
                <span>·</span>
                {when(d.created_at)}
              </p>
            </button>
            <Status status={d.state} />
            <div className="source-row-actions">
              {(d.state === "needs_mapping" || d.state === "mapped") && (
                <button className="secondary small" onClick={() => onMap(d)}>
                  Map columns
                  <ArrowRight size={14} />
                </button>
              )}
              <button
                className="icon-button"
                aria-label={"View " + d.name}
                onClick={() => onView(d)}
              >
                <ArrowsOutSimple size={17} />
              </button>
            </div>
          </article>
        ))}
      </div>
      {!w.documents.length && (
        <button className="upload-zone" onClick={onUpload}>
          <UploadSimple size={35} />
          <h3>Upload your first document</h3>
          <p>PDF, CSV, XLSX, or text · up to 20 MB per file</p>
          <span className="secondary">
            Choose a document
            <Plus size={16} />
          </span>
        </button>
      )}
      <RequirementCoverage w={w} />
      <div className="source-assistant-action">
        <div>
          <h3>Prepare a quote from your sources</h3>
          <p>The assistant proposes changes for you to review.</p>
        </div>
        <button className="secondary" onClick={startAI}>
          <ChatCircleText size={17} /> Open assistant
        </button>
      </div>
    </div>
  );
}
function ChangeCard({
  pr,
  w,
  accept,
  reject,
  openSource,
  busy,
  reanalyze,
}: {
  pr: Proposal;
  w: Workspace;
  accept: () => void;
  reject: () => void;
  openSource: (s: string) => void;
  busy: boolean;
  reanalyze: () => void;
}) {
  const state =
    pr.status === "pending" &&
    (pr.base_version !== w.quote.version ||
      pr.input_revision !== w.quote.input_revision)
      ? "stale"
      : pr.status;
  return (
    <article className={"change-card " + state}>
      <header>
        <div className="change-icon">
          <GitDiff size={22} />
        </div>
        <div>
          <span className="eyebrow">Based on revision {pr.base_version}</span>
          <h3>{pr.title}</h3>
        </div>
        <Status status={state} />
      </header>
      <p className="change-summary">{pr.summary}</p>
      <div className="change-diffs">
        {pr.operations
          .filter((o) => o.type !== "reconcile_inputs")
          .map((op, i) => {
            const l = w.quote.lines.find((l) => l.id === op.line_id);
            const names: Record<string, string> = {
              set_quantity: "Quantity",
              set_lead_time: "Delivery",
              add_line: "New equipment",
              update_description: "Description",
              set_cost: "Supplier cost",
              override_selling_price: "Selling price",
              set_gross_margin: "Gross margin",
              set_markup: "Markup",
            };
            return (
              <div className="change-diff" key={i}>
                <div>
                  <strong>{l?.tag ?? op.line?.tag ?? "Quote"}</strong>
                  <span>{names[op.type] ?? op.type.replaceAll("_", " ")}</span>
                </div>
                <div className="diff-values">
                  <span className="before">
                    {op.type === "add_line"
                      ? "Not in quote"
                      : (op.expected_before ?? "Current value")}
                  </span>
                  <ArrowRight size={17} />
                  <span className="after">
                    {op.type === "add_line"
                      ? `${op.line?.description} · ${op.line?.quantity} ${op.line?.unit}`
                      : op.value}
                  </span>
                </div>
                {op.source_ids?.[0] && (
                  <button
                    className="citation-button"
                    onClick={() => openSource(op.source_ids![0])}
                  >
                    <LinkSimple size={14} />
                    View source
                    <ArrowUpRight size={13} />
                  </button>
                )}
              </div>
            );
          })}
      </div>
      <footer>
        <span>
          <ShieldCheck size={15} />
          {state === "pending"
            ? "Your review authorizes these changes."
            : state === "applied"
              ? "Applied as a new quote revision."
              : state === "stale"
                ? "The baseline changed. Analyze again before accepting."
                : "This proposal was dismissed."}
        </span>
        <div>
          {state === "pending" ? (
            <>
              <button className="secondary" disabled={busy} onClick={reject}>
                Dismiss
              </button>
              <button className="primary" disabled={busy} onClick={accept}>
                <Check size={16} />
                Accept changes
              </button>
            </>
          ) : state === "stale" ? (
            <button className="secondary" onClick={reanalyze}>
              Reanalyze
              <ArrowRight size={14} />
            </button>
          ) : null}
        </div>
      </footer>
    </article>
  );
}

const SOURCE_UUID =
  "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

function AssistantResponse({
  text,
  sourceIds = [],
  w,
  openSource,
}: {
  text: string;
  sourceIds?: string[];
  w: Workspace;
  openSource: (sourceId: string) => void;
}) {
  const inlineIds = [
    ...text.matchAll(new RegExp(`\\[(${SOURCE_UUID})\\]`, "gi")),
  ].map((match) => match[1]);
  const ids = [
    ...new Set([...sourceIds, ...inlineIds].map((id) => id.toLowerCase())),
  ];
  const citations = ids.map((id, index) => {
    const span = w.sources.find(
      (source: any) => source.id.toLowerCase() === id,
    ) as any;
    const document = w.documents.find(
      (document) => document.id === span?.document_id,
    );
    const location = span?.location ?? {};
    const detail = [
      location.sheet,
      location.page
        ? `Page ${location.page}`
        : location.row
          ? `Row ${location.row}`
          : location.line
            ? `Line ${location.line}`
            : location.cells
              ? `Cells ${location.cells}`
              : "",
    ]
      .filter(Boolean)
      .join(" · ");
    return {
      id,
      number: index + 1,
      available: Boolean(span && document),
      label: document
        ? `${document.name}${detail ? ` · ${detail}` : ""}`
        : "Source unavailable",
    };
  });
  const markdown = text.replace(
    new RegExp(`\\[(${SOURCE_UUID})\\]`, "gi"),
    (_, id: string) => {
      const citation = citations.find(
        (citation) => citation.id === id.toLowerCase(),
      )!;
      return `[${citation.number}](#rivet-source-${citation.id})`;
    },
  );
  return (
    <div className="assistant-response">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children }) => {
            const id = href
              ?.match(new RegExp(`^#rivet-source-(${SOURCE_UUID})$`, "i"))?.[1]
              ?.toLowerCase();
            if (id) {
              const citation = citations.find((citation) => citation.id === id);
              return citation?.available ? (
                <button
                  type="button"
                  className="assistant-inline-citation"
                  title={citation.label}
                  aria-label={`Source ${citation.number}: ${citation.label}`}
                  onClick={() => openSource(id)}
                >
                  {citation.number}
                </button>
              ) : (
                <span
                  className="assistant-unavailable-source"
                  title={`Unavailable source: ${id}`}
                >
                  Source unavailable
                </span>
              );
            }
            return href && /^https?:\/\//i.test(href) ? (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            );
          },
          img: ({ alt }) => (
            <span className="assistant-image-description">
              {alt || "Image omitted"}
            </span>
          ),
          table: ({ children }) => (
            <div
              className="assistant-table-scroll"
              role="region"
              aria-label="Assistant response table"
              tabIndex={0}
            >
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {markdown}
      </Markdown>
      {citations.length > 0 && (
        <div className="assistant-citations" aria-label="Answer sources">
          <h5>Sources</h5>
          {citations.map((citation) =>
            citation.available ? (
              <button
                type="button"
                key={citation.id}
                title={citation.label}
                onClick={() => openSource(citation.id)}
              >
                <span>{citation.number}</span>
                <span>{citation.label}</span>
                <ArrowUpRight size={13} />
              </button>
            ) : (
              <p
                key={citation.id}
                className="assistant-unavailable-source"
                title={`Unavailable source: ${citation.id}`}
              >
                <span>{citation.number}</span> Source unavailable in this
                project
              </p>
            ),
          )}
        </div>
      )}
    </div>
  );
}

function Assistant({
  w,
  openSource,
  configured,
  refresh,
  notify,
  selected,
}: {
  w: Workspace;
  openSource: (sourceId: string) => void;
  configured: boolean;
  refresh: () => Promise<void>;
  notify: (s: string) => void;
  selected: string[];
}) {
  const [prompt, setPrompt] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = w.runs[0] as any;
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await api("/quotes/" + w.quote.id + "/agent-runs", {
        goal: prompt,
        selected_lines: selected,
      });
      setPrompt("");
      await refresh();
      notify("Rivet is investigating the project.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const respond = async () => {
    setBusy(true);
    setError("");
    try {
      await api("/agent-runs/" + run.id + "/answers", { answer });
      setAnswer("");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="assistant-panel">
      <div className="assistant-heading">
        <div className="assistant-mark">
          <Mark small />
        </div>
        <div>
          <h3>Bid coordinator</h3>
          <p>Investigate the change and prepare the next action.</p>
        </div>
      </div>
      {!configured && (
        <div className="assistant-offline">
          <Info size={17} />
          <p>
            The AI connection needs server configuration. You can still edit
            quotes and import mapped schedules.
          </p>
        </div>
      )}
      {run && (
        <section className="run-card">
          <div>
            <span className="eyebrow">Latest request</span>
            <Status status={run.status} />
          </div>
          <h4>{run.goal.split("\nSelected")[0]}</h4>
          <p className="run-context">
            Based on revision {run.base_version}
            {run.base_version !== w.quote.version ||
            run.input_revision !== w.quote.input_revision
              ? ". This project has changed since this request."
              : ""}
          </p>
          {(run.plan?.length > 0 || run.steps?.length > 0) && (
            <details className="run-activity">
              <summary>
                View activity <span>{run.steps?.length ?? 0} steps</span>
                <CaretDown size={14} />
              </summary>
              {run.plan?.length > 0 && (
                <ol>
                  {run.plan.map((t: string, i: number) => (
                    <li key={i}>{t}</li>
                  ))}
                </ol>
              )}
              <div className="run-steps">
                {run.steps?.map((s: any, i: number) => (
                  <details key={i}>
                    <summary>
                      {s.result?.error ? (
                        <WarningCircle size={14} />
                      ) : (
                        <CheckCircle size={14} />
                      )}
                      <span>{s.tool.replaceAll("_", " ")}</span>
                      <CaretRight size={12} />
                    </summary>
                    <p>
                      {s.result?.error ??
                        s.result?.summary ??
                        (s.result?.spans
                          ? `Read ${s.result.spans.length} source spans.`
                          : s.result?.staged
                            ? `${s.result.staged} changes staged for review.`
                            : s.result?.price
                              ? "Calculated price: " + usd(s.result.price, 2)
                              : s.result?.valid !== undefined
                                ? s.result.valid
                                  ? "Tentative draft validated."
                                  : s.result.errors?.join(" ")
                                : "Result saved to this run.")}
                    </p>
                  </details>
                ))}
              </div>
            </details>
          )}
          {["queued", "executing", "verifying"].includes(run.status) && (
            <>
              <Busy text="Reviewing project evidence…" />
              <button
                className="text-button"
                onClick={async () => {
                  try {
                    await api("/agent-runs/" + run.id + "/cancel", {});
                    await refresh();
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                Stop this run
              </button>
            </>
          )}
          {run.status === "waiting_for_input" && (
            <div className="run-questions">
              <h4>A detail needs your input</h4>
              {run.questions.map((s: string, i: number) => (
                <AssistantResponse
                  key={i}
                  text={s}
                  w={w}
                  openSource={openSource}
                />
              ))}
              <textarea
                aria-label="Answer the assistant"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                placeholder="Add the missing information…"
              />
              <button
                className="primary"
                disabled={!answer.trim() || busy}
                onClick={respond}
              >
                Save answer & resume
                <ArrowRight size={14} />
              </button>
            </div>
          )}
          {run.result?.error && <ErrorNote message={run.result.error} />}{" "}
          {run.result?.summary && (
            <AssistantResponse
              text={run.result.summary}
              sourceIds={run.result.source_ids}
              w={w}
              openSource={openSource}
            />
          )}
          {run.result?.clarifications?.length > 0 && (
            <div className="clarifications">
              <h4>Draft clarification</h4>
              {run.result.clarifications.map((c: string, i: number) => (
                <AssistantResponse
                  key={i}
                  text={c}
                  w={w}
                  openSource={openSource}
                />
              ))}
              <button
                className="text-button"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(
                      run.result.clarifications.join("\n\n"),
                    );
                    notify("Clarification copied.");
                  } catch {
                    setError(
                      "Clipboard unavailable. Select and copy the text above.",
                    );
                  }
                }}
              >
                <Copy size={14} />
                Copy text
              </button>
            </div>
          )}
        </section>
      )}
      {!run && (
        <div className="assistant-suggestions">
          <p>Try a request</p>
          {[
            "Review this change against the existing switchgear quote.",
            "What changed in the latest addendum?",
            "Find missing prices and uncovered requirements.",
          ].map((s) => (
            <button key={s} onClick={() => setPrompt(s)}>
              {s}
              <ArrowUpRight size={15} />
            </button>
          ))}
        </div>
      )}
      <form
        className="assistant-composer"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <textarea
          aria-label="Ask Rivet about this project"
          placeholder="Ask Rivet about this quote…"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <div>
          <span>
            {selected.length
              ? `${selected.length} selected lines`
              : "This project only"}
          </span>
          <button
            className="send-button"
            aria-label="Send request to Rivet"
            disabled={
              !configured ||
              !prompt.trim() ||
              busy ||
              ["queued", "executing"].includes(run?.status)
            }
          >
            <PaperPlaneRight size={17} />
          </button>
        </div>
      </form>
      {error && <ErrorNote message={error} />}
      <p className="assistant-note">
        <ShieldCheck size={13} />
        Rivet proposes. You approve.
      </p>
    </div>
  );
}

function LineModal({
  line,
  onClose,
  onSave,
}: {
  line?: Line;
  onClose: () => void;
  onSave: (value: any) => Promise<void>;
}) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <Modal
      title={line ? "Edit " + line.tag : "Add equipment"}
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          const f = new FormData(e.currentTarget);
          const value: any = Object.fromEntries(f);
          value.cost = value.cost || null;
          value.price = value.price || null;
          value.source_ids = [];
          if (!line) delete value.reason;
          try {
            await onSave(value);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-row">
          <label>
            Equipment tag
            <input
              name="tag"
              defaultValue={line?.tag}
              required
              placeholder="PDU-01"
              readOnly={!!line}
            />
          </label>
          <label>
            Quantity
            <input
              name="quantity"
              type="number"
              min="0.0001"
              step="0.0001"
              defaultValue={line?.quantity ?? "1"}
              required
            />
          </label>
        </div>
        <label>
          Description
          <input
            name="description"
            defaultValue={line?.description}
            required
            placeholder="Equipment description"
          />
        </label>
        <div className="form-row">
          <label>
            Quantity / price unit
            <select
              name="unit"
              defaultValue={line?.unit ?? "each"}
              disabled={!!line}
            >
              <option value="each">Each</option>
              <option value="ft">Per foot</option>
              <option value="package">Per package</option>
            </select>
          </label>
          <label>
            Model / configuration
            <input
              name="model"
              defaultValue={line?.model}
              placeholder="Optional — confirm selection"
              readOnly={!!line}
            />
            {line && (
              <small className="field-note">
                Use Select from catalog in equipment details to change this
                configuration.
              </small>
            )}
          </label>
        </div>
        <div className="form-row">
          <label>
            Supplier cost · USD
            <input
              name="cost"
              type="number"
              min="0"
              step="0.01"
              defaultValue={line?.cost ?? ""}
              placeholder="Unknown"
            />
          </label>
          <label>
            Selling price · USD
            <input
              name="price"
              type="number"
              min="0"
              step="0.01"
              defaultValue={line?.price ?? ""}
              placeholder="Unknown"
            />
          </label>
        </div>
        <label>
          Delivery wording
          <input
            name="lead_time"
            defaultValue={line?.lead_time ?? "Needs confirmation"}
          />
        </label>
        {line && (
          <label>
            Reason for this edit
            <input
              name="reason"
              required
              placeholder="Record the source or reason for your change"
            />
          </label>
        )}
        <p className="field-note">
          Unknown prices remain blank. Manual values are recorded as human
          entered.
        </p>
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy}>
            {busy ? <Busy /> : line ? "Save revision" : "Add line"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
function Pricing({
  count,
  onClose,
  onSave,
}: {
  count: number;
  onClose: () => void;
  onSave: (basis: string, percent: string) => Promise<void>;
}) {
  const [basis, setBasis] = useState("margin"),
    [percent, setPercent] = useState("20"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal
      title="Set pricing"
      eyebrow={`${count} equipment line${count !== 1 ? "s" : ""}`}
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            await onSave(basis, percent);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="pricing-choices">
          <button
            type="button"
            className={basis === "margin" ? "selected" : ""}
            onClick={() => setBasis("margin")}
          >
            <strong>Gross margin</strong>
            <small>Cost ÷ (1 − margin)</small>
          </button>
          <button
            type="button"
            className={basis === "markup" ? "selected" : ""}
            onClick={() => setBasis("markup")}
          >
            <strong>Markup</strong>
            <small>Cost × (1 + markup)</small>
          </button>
        </div>
        <label>
          {basis === "margin" ? "Gross margin" : "Markup"} percentage
          <input
            type="number"
            value={percent}
            min="0"
            max={basis === "margin" ? "99.99" : "1000"}
            step="0.01"
            required
            onChange={(e) => setPercent(e.target.value)}
          />
        </label>
        <div className="pricing-example">
          <span>Example at {percent || 0}%</span>
          <p>
            $8,000 supplier cost <ArrowRight size={17} />
            <strong>
              {usd(
                basis === "margin"
                  ? 8000 / (1 - Number(percent) / 100)
                  : 8000 * (1 + Number(percent) / 100),
                2,
              )}
            </strong>{" "}
            selling price
          </p>
        </div>
        <p className="field-note">
          The server recalculates prices with exact decimal arithmetic. Each
          changed line needs a fresh review.
        </p>
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy}>
            {busy ? <Busy /> : "Apply pricing"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
function Upload({
  id,
  onClose,
  onDone,
  initialKind,
}: {
  id: string;
  onClose: () => void;
  onDone: () => Promise<void>;
  initialKind: string;
}) {
  const [file, setFile] = useState<File | null>(null),
    [kind, setKind] = useState(initialKind),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  return (
    <Modal title="Upload document" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (!file) return;
          setBusy(true);
          setError("");
          try {
            if (file.size > 20 * 1024 * 1024)
              throw new Error("Choose a file smaller than 20 MB.");
            const f = new FormData();
            f.append("file", file);
            f.append("kind", kind);
            await api("/projects/" + id + "/documents", f);
            await onDone();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          Document type
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="schedule">Equipment schedule</option>
            <option value="specification">Specification / bid package</option>
            <option value="addendum">Addendum / revision</option>
            <option value="offer">Supplier offer</option>
            <option value="catalog">Catalog / price list</option>
          </select>
        </label>
        <input
          ref={input}
          className="sr-only"
          type="file"
          accept=".pdf,.csv,.xlsx,.txt"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          aria-label="Select source document"
        />
        <button
          type="button"
          className={"upload-zone modal-upload " + (drag ? "dragging" : "")}
          onClick={() => input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            setFile(e.dataTransfer.files[0] ?? null);
          }}
        >
          <UploadSimple size={32} />
          <h3>{file ? file.name : "Drop a source document here"}</h3>
          <p>
            {file
              ? `${(file.size / 1024).toFixed(1)} KB · click to choose another`
              : "or click to browse · PDF, CSV, XLSX, TXT"}
          </p>
        </button>
        <p className="field-note">
          Text-based PDFs only. Scans and unreadable sections will be flagged
          for review. Uploading invalidates the current approval.
        </p>
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <button className="secondary" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={!file || busy}>
            {busy ? (
              <Busy text="Uploading…" />
            ) : (
              <>
                Upload source
                <ArrowRight size={16} />
              </>
            )}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
function Paste({
  id,
  onClose,
  onDone,
}: {
  id: string;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal title="Paste request" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const data = Object.fromEntries(new FormData(e.currentTarget));
          setBusy(true);
          setError("");
          try {
            await api("/projects/" + id + "/text", data);
            await onDone();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          Source title
          <input
            name="name"
            required
            placeholder="e.g. Customer clarification — 28 Sep"
          />
        </label>
        <div className="form-row">
          <label>
            Sender
            <input name="sender" required placeholder="Enter explicitly" />
          </label>
          <label>
            Source date
            <input name="date" type="date" required />
          </label>
        </div>
        <label>
          Source type
          <select name="kind">
            <option value="schedule">Request / schedule</option>
            <option value="addendum">Addendum</option>
            <option value="offer">Supplier offer</option>
            <option value="specification">Specification</option>
          </select>
        </label>
        <label>
          Request text
          <textarea
            name="text"
            required
            rows={7}
            placeholder="Paste the original request or email body…"
          />
        </label>
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <button className="secondary" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy}>
            {busy ? <Busy /> : "Save source"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
function TextModal({
  title,
  label,
  initial,
  hint,
  onClose,
  onSave,
}: {
  title: string;
  label: string;
  initial: string;
  hint?: string;
  onClose: () => void;
  onSave: (v: string) => Promise<void>;
}) {
  const [value, setValue] = useState(initial),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <Modal title={title} onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await onSave(value);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          {label}
          <textarea
            rows={6}
            required
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </label>
        {hint && <p className="field-note">{hint}</p>}
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy}>
            {busy ? <Busy /> : "Save review"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
function MappingModal({
  doc,
  inputRevision,
  onClose,
  onDone,
}: {
  doc: Doc;
  inputRevision: number;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const meta = doc.meta as any;
  const [mapping, setMapping] = useState<Record<string, string>>(
      meta.confirmed_mapping ?? meta.suggested_mapping ?? {},
    ),
    [purpose, setPurpose] = useState(
      meta.purpose ??
        (doc.kind === "addendum"
          ? "addendum"
          : doc.kind === "catalog"
            ? "catalog"
            : doc.kind === "offer"
              ? "offers"
              : "schedule"),
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const fields: Record<string, string> = {
    tag: "Equipment tag",
    description: "Description",
    quantity: "Quantity",
    unit: "Quantity / price unit",
    model: "Model",
    cost: "Supplier cost",
    price: "Selling price",
    lead_time: "Delivery wording",
    manufacturer: "Manufacturer",
    supplier: "Supplier",
    valid_until: "Offer expiry (YYYY-MM-DD)",
  };
  const visible =
    purpose === "catalog"
      ? ["model", "manufacturer", "description"]
      : purpose === "offers"
        ? ["model", "supplier", "cost", "unit", "valid_until", "lead_time"]
        : [
            "tag",
            "description",
            "quantity",
            "unit",
            "model",
            "cost",
            "price",
            "lead_time",
          ];
  return (
    <Modal title="Map columns" eyebrow={doc.name} onClose={onClose} wide>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            await api("/documents/" + doc.id + "/mapping", {
              expected_input_revision: inputRevision,
              mapping: Object.fromEntries(
                Object.entries(mapping).filter(
                  ([k, v]) => visible.includes(k) && v,
                ),
              ),
              purpose,
            });
            await onDone();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="modal-intro">
          Review the suggested matches. Blank prices stay unknown; repeated
          equipment tags are not added together.
        </p>
        <label>
          Import as
          <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
            <option value="schedule">Equipment schedule → pending draft</option>
            <option value="addendum">Addendum → proposed changes</option>
            <option value="quote">Existing quote → pending draft</option>
            <option value="catalog">Equipment catalog</option>
            <option value="offers">Supplier offers</option>
          </select>
        </label>
        <div className="mapping-grid">
          {visible.map((field) => (
            <label key={field}>
              {fields[field]}
              <select
                value={mapping[field] ?? ""}
                onChange={(e) =>
                  setMapping({ ...mapping, [field]: e.target.value })
                }
              >
                <option value="">Not mapped</option>
                {meta.headers?.map((h: string) => (
                  <option key={h} value={h}>
                    {h}
                  </option>
                ))}
              </select>
              <small>
                {mapping[field]
                  ? "Example: " +
                    (meta.rows?.[0]?.values[mapping[field]] || "Blank")
                  : "No source value"}
              </small>
            </label>
          ))}
        </div>
        <div className="info-banner">
          <Info size={17} />
          <p>
            {purpose === "addendum"
              ? "You are identifying this document as an authorized addendum. Only explicit changed values are proposed; omitted equipment is preserved."
              : "Confirm unit meanings and price bases before importing. The prototype supports USD, and each, foot, or package units."}
          </p>
        </div>
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <span>{meta.rows?.length ?? 0} source rows</span>
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy}>
            {busy ? <Busy /> : "Confirm mapping"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}

function DocumentViewer({
  doc,
  sources,
  sourceId,
  onClose,
  onMap,
}: {
  doc: Doc;
  sources: any[];
  sourceId?: string;
  onClose: () => void;
  onMap: () => void;
}) {
  const docSources = sources.filter((s) => s.document_id === doc.id);
  const selected = docSources.find((s) => s.id === sourceId);
  const [page, setPage] = useState(selected?.location?.page ?? 1),
    [pages, setPages] = useState(1),
    [error, setError] = useState("");
  const canvas = useRef<HTMLCanvasElement>(null);
  const pdfRef = useRef<any>(null);
  const isPDF = doc.name.toLowerCase().endsWith(".pdf");
  useEffect(() => {
    if (!isPDF) return;
    let cancelled = false;
    let loading: any;
    void (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        loading = pdfjs.getDocument({
          httpHeaders: await authorizationHeaders(),
          url: "/api/documents/" + doc.id + "/content",
        });
        const pdf = await loading.promise;
        if (cancelled) {
          pdf.destroy();
          return;
        }
        pdfRef.current = pdf;
        setPages(pdf.numPages);
        const p = await pdf.getPage(page);
        const v = p.getViewport({ scale: 1.2 });
        if (canvas.current) {
          canvas.current.height = v.height;
          canvas.current.width = v.width;
          await p.render({ canvas: canvas.current, viewport: v }).promise;
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
      loading?.destroy();
    };
  }, [doc.id, isPDF, page]);
  return (
    <Modal title={doc.name} eyebrow="Original document" onClose={onClose} wide>
      <div className="document-toolbar">
        <Status status={doc.state} />
        <a
          className="text-button"
          href={"/api/documents/" + doc.id + "/content"}
          target="_blank"
          rel="noreferrer"
        >
          Open original
          <ArrowUpRight size={14} />
        </a>
        {doc.state === "needs_mapping" && (
          <button className="primary small" onClick={onMap}>
            Map columns
            <ArrowRight size={14} />
          </button>
        )}
      </div>
      <div className="coverage-strip">
        {doc.coverage.map((c: any, i) => (
          <span
            key={i}
            className={
              ["scanned", "failed", "unsupported"].includes(c.state)
                ? "bad"
                : ""
            }
          >
            <span className="online-dot" />
            {c.label} · {c.state}
            {c.detail && <small>{c.detail}</small>}
          </span>
        ))}
      </div>
      {error && <ErrorNote message={error} />}{" "}
      {isPDF ? (
        <>
          <div className="pdf-pages">
            <button
              className="secondary small"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
            >
              Previous
            </button>
            <span>
              Page {page} / {pages}
            </span>
            <button
              className="secondary small"
              disabled={page >= pages}
              onClick={() => setPage(page + 1)}
            >
              Next
            </button>
          </div>
          <div className="pdf-wrap">
            <canvas ref={canvas} />
            {selected?.location?.bbox && selected.location.page === page && (
              <div
                className="pdf-highlight"
                style={{
                  left: selected.location.bbox[0] * 100 + "%",
                  top: selected.location.bbox[1] * 100 + "%",
                  width:
                    (selected.location.bbox[2] - selected.location.bbox[0]) *
                      100 +
                    "%",
                  height:
                    (selected.location.bbox[3] - selected.location.bbox[1]) *
                      100 +
                    "%",
                }}
              />
            )}
          </div>
          <p className="field-note">
            Highlighted regions are approximate. Review the original page
            context.
          </p>
        </>
      ) : (
        <div className="source-transcript">
          {docSources.map((s, i) => (
            <div className={s.id === sourceId ? "highlighted" : ""} key={s.id}>
              <span>{s.location.cells ?? s.location.line ?? i + 1}</span>
              <p>{s.text}</p>
            </div>
          ))}
          {!docSources.length && (
            <Empty
              title={
                doc.state === "queued"
                  ? "Reading this document…"
                  : "No extractable text"
              }
            >
              {doc.state === "queued"
                ? "Close this view and return after processing finishes."
                : "Review the original and supply a transcription for unsupported sections."}
            </Empty>
          )}
        </div>
      )}
    </Modal>
  );
}

function History({
  w,
  refresh,
  notify,
}: {
  w: Workspace;
  refresh: () => Promise<void>;
  notify: (s: string) => void;
}) {
  const [target, setTarget] = useState<any>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <div className="history-page">
      <div className="section-heading">
        <div>
          <h2>Revision history</h2>
          <p>
            Each revision is an immutable snapshot. Restoring one creates a new
            draft.
          </p>
        </div>
      </div>
      <div className="revision-list">
        {w.history.map((v: any) => (
          <article key={v.id}>
            <div className="revision-number">
              {String(v.version).padStart(2, "0")}
            </div>
            <div>
              <h3>
                {v.summary}
                {v.version === w.quote.version && (
                  <span className="micro-tag">Current</span>
                )}
              </h3>
              <p>
                {v.actor}
                <span>·</span>
                {when(v.at)}
                <span>·</span>Input revision {v.input_revision}
              </p>
            </div>
            <strong>{usd(v.total)}</strong>
            {v.version !== w.quote.version && (
              <button className="quiet-button" onClick={() => setTarget(v)}>
                <ArrowCounterClockwise size={15} />
                Restore
              </button>
            )}
          </article>
        ))}
      </div>
      <h3 className="history-subheading">Project activity</h3>
      <div className="compact-events">
        {w.events.map((e: any) => (
          <div key={e.id}>
            <span className="event-dot" />
            <div>
              <p>{e.summary}</p>
              <span>
                {e.actor} · {when(e.at)}
              </span>
            </div>
          </div>
        ))}
      </div>
      {target && (
        <Modal
          title={"Restore revision " + target.version + "?"}
          onClose={() => setTarget(null)}
        >
          <p className="modal-intro">
            This creates a new draft with revision {target.version}’s values.
            Each line will need review again. Restoration is blocked if the
            source revision changed.
          </p>
          {error && <ErrorNote message={error} />}
          <footer className="modal-actions">
            <button className="secondary" onClick={() => setTarget(null)}>
              Cancel
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await api("/quotes/" + w.quote.id + "/revert", {
                    expected_version: w.quote.version,
                    expected_input_revision: w.quote.input_revision,
                    target_version: target.version,
                    reason: "Restore reviewed historical values",
                  });
                  await refresh();
                  notify("Historical values restored as a new draft.");
                  setTarget(null);
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? <Busy /> : "Create restored draft"}
            </button>
          </footer>
        </Modal>
      )}
    </div>
  );
}

function ExportReview({
  w,
  onClose,
  refresh,
  notify,
  onFix,
}: {
  w: Workspace;
  onClose: () => void;
  refresh: () => Promise<void>;
  notify: (s: string) => void;
  onFix: (c: any) => void;
}) {
  const q = w.quote,
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [consent, setConsent] = useState(false);
  const request = {
    expected_version: q.version,
    expected_input_revision: q.input_revision,
    reason: "Reviewed customer-facing quote, scope, pricing and terms",
  };
  const exportFile = async (format: string) => {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ url: string }>(
        "/quotes/" + q.id + "/exports?format=" + format,
        request,
      );
      const a = document.createElement("a");
      a.href = result.url;
      a.download = "";
      a.click();
      await refresh();
      notify("Approved " + format.toUpperCase() + " is ready.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Review & export"
      eyebrow={`Customer quote · Revision ${q.version}`}
      onClose={onClose}
      wide
    >
      <div className="export-layout">
        <div className="quote-paper">
          <header>
            <span>
              <Mark small />
              rivet.
            </span>
            <div>
              EQUIPMENT QUOTE
              <br />
              {q.number} / R{String(q.version).padStart(2, "0")}
            </div>
          </header>
          {q.synthetic && (
            <div className="paper-demo">
              SYNTHETIC DEMONSTRATION · NOT A COMMERCIAL OFFER
            </div>
          )}
          <h2>{w.project.title}</h2>
          <p>Prepared for {w.project.customer}</p>
          <table>
            <thead>
              <tr>
                <th>Equipment</th>
                <th>Qty</th>
                <th>Unit price</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {q.lines.map((l) => (
                <tr key={l.id}>
                  <td>
                    <b>{l.tag}</b>
                    <span>{l.description}</span>
                    <small>{l.model}</small>
                  </td>
                  <td>
                    {l.quantity} {l.unit}
                  </td>
                  <td>{usd(l.price)}</td>
                  <td>{usd(l.extended)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="paper-total">
            <span>Total USD</span>
            <strong>{usd(q.total, 2)}</strong>
          </div>
          <h4>Delivery</h4>
          {q.lines.map((l) => (
            <p className="paper-delivery" key={l.id}>
              <b>{l.tag}</b> — {l.lead_time}
            </p>
          ))}
          <h4>Terms & exclusions</h4>
          <p>{q.terms}</p>
          <footer>
            Rivet · Revision {q.version} · Customer-facing preview
          </footer>
        </div>
        <aside className="export-controls">
          <div className="export-safe">
            <ShieldCheck size={23} />
            <h3>Commercial review</h3>
            <p>
              Customer files include selling prices, scope, delivery wording,
              and terms. Internal costs and margin are excluded.
            </p>
          </div>
          <Status status={q.status} />
          {q.checks.length > 0 ? (
            <div className="export-checks">
              <h4>{q.checks.length} items to resolve</h4>
              {q.checks.map((c, i) => (
                <button key={i} onClick={() => onFix(c)}>
                  <WarningCircle size={15} />
                  <span>{c.message}</span>
                  <CaretRight size={13} />
                </button>
              ))}
            </div>
          ) : q.status !== "approved" ? (
            <>
              <label className="approval-consent">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                />
                <span>
                  I reviewed the equipment, selling prices, delivery wording,
                  scope, and terms for this revision.
                </span>
              </label>
              <button
                className="primary"
                disabled={!consent || busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    await api("/quotes/" + q.id + "/approve", request);
                    await refresh();
                    notify("Revision approved. Customer exports are enabled.");
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <CheckCircle size={17} />
                Approve revision {q.version}
              </button>
            </>
          ) : (
            <p className="approval-ok">
              <CheckCircle size={16} />
              This revision is approved.
            </p>
          )}
          <div className="download-actions">
            <button
              className="primary"
              disabled={q.status !== "approved" || q.checks.length > 0 || busy}
              onClick={() => void exportFile("pdf")}
            >
              <DownloadSimple size={16} />
              Download PDF
            </button>
            <button
              className="secondary"
              disabled={q.status !== "approved" || q.checks.length > 0 || busy}
              onClick={() => void exportFile("xlsx")}
            >
              <DownloadSimple size={16} />
              Download XLSX
            </button>
          </div>
          {busy && <Busy text="Preparing your revision…" />}
          {error && <ErrorNote message={error} />}
          <p className="field-note">
            Exporting saves a file from the approved snapshot. It does not send
            the quote.
          </p>
        </aside>
      </div>
    </Modal>
  );
}
function SelectProduct({
  onClose,
  onSave,
}: {
  onClose: () => void;
  onSave: (id: string) => Promise<void>;
}) {
  const { data } = useQuery({
    queryKey: ["catalog"],
    queryFn: () => api<any[]>("/catalog"),
  });
  const [query, setQuery] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal title="Choose catalog equipment" onClose={onClose}>
      <p className="modal-intro">
        Select the exact model. Rivet attaches a current offer only when its
        units, currency, and quantity coverage match.
      </p>
      <label>
        Find equipment
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Model or description"
        />
      </label>
      <div className="product-choices">
        {data
          ?.filter((c) =>
            (c.model + " " + c.description)
              .toLowerCase()
              .includes(query.toLowerCase()),
          )
          .map((c) => (
            <button
              key={c.id}
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  await onSave(c.id);
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <Stack size={20} />
              <span>
                <strong>{c.model}</strong>
                <small>{c.description}</small>
              </span>
              <span>
                {usd(c.offers[0]?.cost)}
                <ArrowRight size={14} />
              </span>
            </button>
          ))}
      </div>
      {error && <ErrorNote message={error} />}
    </Modal>
  );
}
function RequirementCoverage({ w }: { w: Workspace }) {
  if (!w.requirements.length) return null;
  return (
    <section className="coverage-section">
      <div>
        <h3>Requirement coverage</h3>
      </div>
      <div className="coverage-table">
        {w.requirements.map((r: any) => {
          const line = w.quote.lines.find((l) => l.tag === r.tag);
          const match =
            line && Number(line.quantity) === Number(r.value.quantity);
          return (
            <div key={r.id}>
              <code>{r.tag}</code>
              <span>
                {r.value.quantity} {r.value.unit ?? "each"} required
              </span>
              <span>
                {line
                  ? `${line.quantity} ${line.unit} quoted`
                  : "No quote line"}
              </span>
              <span className={match ? "coverage-pass" : "coverage-fail"}>
                {match ? (
                  <CheckCircle size={14} />
                ) : (
                  <WarningCircle size={14} />
                )}{" "}
                {match ? "Covered" : line ? "Quantity differs" : "Uncovered"}
              </span>
            </div>
          );
        })}
      </div>
      <p>
        Coverage compares explicit equipment tags and quantities. Product
        suitability still needs estimator review.
      </p>
    </section>
  );
}
