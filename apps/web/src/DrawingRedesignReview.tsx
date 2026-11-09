import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowSquareOut,
  Check,
  CircleNotch,
  CaretDown,
  FileText,
  MagnifyingGlassPlus,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api, apiFetch } from "./api";
import { ErrorNote } from "./ui";
import {
  redesignApiPath,
  redesignCanApprove,
  redesignIsActive,
  redesignStatusLabel,
  type DrawingRedesignChange,
  type DrawingRedesignRun,
  type DrawingRedesignView,
} from "./drawing-redesign-types";
import "./drawing-redesign-review.css";

function Comparison({ change }: { change: DrawingRedesignChange }) {
  const [images, setImages] = useState<{
    before?: string;
    after?: string;
    error?: string;
  }>({});
  const [expanded, setExpanded] = useState<"before" | "after" | null>(null);
  const triggerRefs = useRef<
    Record<"before" | "after", HTMLButtonElement | null>
  >({
    before: null,
    after: null,
  });
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let active = true;
    const objectUrls: string[] = [];
    const controller = new AbortController();
    setImages({});
    async function load(url: string) {
      const response = await apiFetch(redesignApiPath(url), {
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("A drawing comparison could not load.");
      const blob = await response.blob();
      if (
        !blob.size ||
        !response.headers.get("content-type")?.startsWith("image/")
      )
        throw new Error("The comparison file is not a drawing image.");
      const objectUrl = URL.createObjectURL(blob);
      objectUrls.push(objectUrl);
      return objectUrl;
    }
    void Promise.all([
      load(change.comparison.before_url),
      load(change.comparison.after_url),
    ])
      .then(([before, after]) => {
        if (active) setImages({ before, after });
      })
      .catch((error: unknown) => {
        if (active && !controller.signal.aborted)
          setImages({ error: (error as Error).message });
      });
    return () => {
      active = false;
      controller.abort();
      objectUrls.forEach(URL.revokeObjectURL);
    };
  }, [change.comparison.before_url, change.comparison.after_url]);

  useEffect(() => {
    if (!expanded) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(null);
      if (event.key === "Tab") {
        event.preventDefault();
        closeRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      triggerRefs.current[expanded]?.focus();
    };
  }, [expanded]);

  return (
    <>
      <div
        className="drr-comparison"
        aria-label={`${change.label} before and after`}
      >
        {(["before", "after"] as const).map((side) => (
          <figure key={side}>
            <figcaption>
              <span>{side === "before" ? "Before" : "After"}</span>
              <strong>{change[side]}</strong>
            </figcaption>
            {images[side] ? (
              <button
                className="drr-image-button"
                ref={(element) => {
                  triggerRefs.current[side] = element;
                }}
                type="button"
                aria-label={`Expand ${side} drawing for ${change.label}`}
                onClick={() => setExpanded(side)}
              >
                <img
                  src={images[side]}
                  alt={`${side} drawing: ${change.label}`}
                />
                <span>
                  <MagnifyingGlassPlus size={15} /> Expand
                </span>
              </button>
            ) : (
              <div
                className={`drr-image-empty${images.error ? " has-error" : ""}`}
              >
                {images.error ? (
                  <WarningCircle size={17} />
                ) : (
                  <CircleNotch size={17} className="drr-spin" />
                )}
                <span>{images.error || "Loading comparison…"}</span>
              </div>
            )}
          </figure>
        ))}
      </div>
      {images.error && <p className="drr-inline-error">{images.error}</p>}
      {expanded &&
        images[expanded] &&
        createPortal(
          <div
            className="drr-lightbox"
            role="dialog"
            aria-modal="true"
            aria-label={`${expanded} drawing: ${change.label}`}
            onClick={() => setExpanded(null)}
          >
            <button
              type="button"
              className="drr-lightbox-close"
              ref={closeRef}
              aria-label="Close enlarged drawing"
              onClick={() => setExpanded(null)}
            >
              <X size={20} />
            </button>
            <img
              src={images[expanded]}
              alt={`${expanded} drawing: ${change.label}`}
              onClick={(event) => event.stopPropagation()}
            />
            <span>
              {expanded === "before" ? "Before" : "After"} · {change.label}
            </span>
          </div>,
          document.body,
        )}
    </>
  );
}

function commentStatus(status: string) {
  return (
    {
      changed: "Changed",
      already_present: "Already present",
      needs_information: "Needs information",
      not_applicable: "Not applicable",
    }[status] || status
  );
}

async function download(path: string, filename: string) {
  const response = await apiFetch(redesignApiPath(path));
  if (!response.ok) throw new Error(`Could not download ${filename}.`);
  const blob = await response.blob();
  if (!blob.size) throw new Error(`${filename} is empty.`);
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  // Let the browser start saving large native drawings before releasing them.
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
}

export function DrawingRedesignReview({
  id,
  initialOpen = false,
  screen = "all",
  onViewChanges,
  onOpenSource,
}: {
  id: string;
  initialOpen?: boolean;
  screen?: "all" | "requests" | "changes";
  onViewChanges?: (changeId: string) => void;
  onOpenSource?: (documentId: string, page?: number | null) => void;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(initialOpen);
  const [busy, setBusy] = useState<"prepare" | "approve" | "request" | "">("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [changeIndex, setChangeIndex] = useState(0);
  const [requestFilter, setRequestFilter] = useState<"all" | "open" | "edits">(
    "all",
  );
  const query = useQuery({
    queryKey: ["drawing-redesign", id],
    queryFn: () =>
      api<DrawingRedesignView>(`/orders/${id}/engineering/redesign`),
    enabled: !!id,
    retry: false,
    refetchInterval: (q) =>
      q.state.data?.latest && redesignIsActive(q.state.data.latest.status)
        ? 2500
        : open
          ? 10000
          : false,
  });
  const view = query.data;
  const run = view?.latest;
  const requestedChange = new URLSearchParams(
    location.hash.split("?")[1] || "",
  ).get("change");
  useEffect(() => {
    setChangeIndex(
      Math.max(
        0,
        run?.changes.findIndex((change) => change.id === requestedChange) ?? 0,
      ),
    );
  }, [id, run?.id, requestedChange]);
  const changesByComment = useMemo(() => {
    const map = new Map<string, DrawingRedesignChange[]>();
    for (const change of run?.changes || []) {
      map.set(change.comment_id, [
        ...(map.get(change.comment_id) || []),
        change,
      ]);
    }
    return map;
  }, [run?.changes]);

  async function prepare() {
    if (!view || busy) return;
    setBusy("prepare");
    setError("");
    try {
      await api<DrawingRedesignView>(
        `/orders/${id}/engineering/redesign/prepare`,
        { expected_version: view.order_version },
      );
      await queryClient.invalidateQueries({
        queryKey: ["drawing-redesign", id],
      });
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function decide(decision: "approve" | "request_changes") {
    if (!view || !run || busy) return;
    setBusy(decision === "approve" ? "approve" : "request");
    setError("");
    try {
      await api<DrawingRedesignView>(
        `/orders/${id}/engineering/redesign/${run.id}/decision`,
        {
          expected_version: view.order_version,
          decision,
          note: note.trim(),
        },
      );
      setNote("");
      await queryClient.invalidateQueries({
        queryKey: ["drawing-redesign", id],
      });
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy("");
    }
  }

  if (query.isError)
    return (
      <ErrorNote message="Drawing review could not load. Refresh to try again." />
    );
  if (!view || (!view.source_ready && !run)) return null;
  const active = !!run && redesignIsActive(run.status);
  const canApprove = !!run && redesignCanApprove(run);
  const dxf = run?.artifacts.find(
    (a) => /dxf/i.test(a.media_type) || /\.dxf$/i.test(a.name),
  );
  const reviewPackage = run?.artifacts.find((a) =>
    /review.?package|bundle/i.test(`${a.role} ${a.name}`),
  );
  const reviewPdf = run?.artifacts.find((a) => /\.pdf$/i.test(a.name));
  const selectedChange =
    run?.changes[Math.min(changeIndex, Math.max(0, run.changes.length - 1))];
  const selectedComment = run?.comments.find(
    (c) => c.id === selectedChange?.comment_id,
  );
  const canPrepare =
    view.source_ready &&
    !active &&
    (!run ||
      !run.current ||
      run.status === "failed" ||
      run.status === "changes_requested");

  return (
    <section className="drr-entry" aria-label="Drawing redesign review">
      {screen === "all" && (
        <div className="drr-entry-row">
          <div className="drr-entry-copy">
            <span className="drr-entry-mark" aria-hidden="true">
              R
            </span>
            <div>
              <strong>Drawing redesign</strong>
              <p>
                {run
                  ? `${run.changes.length} native edits · ${run.totals.already_present} requests already present · ${run.totals.needs_information} open requests`
                  : "Review the supplied drawing comments and prepare a revision."}
              </p>
            </div>
          </div>
          <button
            type="button"
            className="drr-toggle"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? "Close review" : run ? "Open review" : "Review comments"}
            <ArrowSquareOut size={15} />
          </button>
        </div>
      )}
      {query.isError && (
        <ErrorNote message="Drawing redesign status could not load." />
      )}
      {open && (
        <div className="drr-panel">
          {screen === "all" && (
            <header className="drr-panel-head">
              <div>
                <span className="drr-eyebrow">ENGINEERING REVIEW</span>
                <h2>Drawing redesign</h2>
                <p>
                  Compare the saved changes, download the drawing, and review
                  the customer requests.
                </p>
              </div>
              {run && (
                <div className="drr-status-group">
                  {!run.current && (
                    <span className="drr-status is-stale">
                      Prior revision · stale
                    </span>
                  )}
                  <span className={`drr-status is-${run.status}`}>
                    {redesignStatusLabel(run.status)}
                  </span>
                </div>
              )}
            </header>
          )}
          {error && <ErrorNote message={error} />}
          {run?.error && (
            <div className="drr-run-error">
              <WarningCircle size={17} />
              {run.error}
            </div>
          )}
          {active && (
            <div className="drr-working" role="status">
              <CircleNotch size={17} className="drr-spin" />
              Rivet is applying the drawing comments and checking the changed
              files.
            </div>
          )}
          {run && (
            <>
              {screen === "changes" && (
                <details className="drr-run-detail">
                  <summary>
                    <span>
                      <strong>{run.changes.length}</strong> saved edits
                    </span>
                    <span>
                      {run.totals.needs_information} requests need input
                    </span>
                    {!run.current && <span>Prior revision</span>}
                    <CaretDown size={13} />
                  </summary>
                  <p>{run.summary}</p>
                </details>
              )}
              {screen === "all" && (
                <div className="drr-summary">
                  <p>{run.summary}</p>
                  <div className="drr-totals" aria-label="Comment totals">
                    <span>
                      <b>{run.totals.comments}</b> comments
                    </span>
                    <span>
                      <b>{run.changes.length}</b> native edits
                    </span>
                    <span>
                      <b>{run.totals.already_present}</b> already present
                    </span>
                    <span>
                      <b>{run.totals.needs_information}</b> need information
                    </span>
                  </div>
                </div>
              )}
              {screen !== "requests" && !!run.artifacts.length && (
                <div className="drr-exports">
                  <div>
                    <strong>Revision files</strong>
                  </div>
                  <div className="drr-downloads">
                    {dxf && (
                      <button
                        type="button"
                        onClick={() =>
                          void download(dxf.url, dxf.name).catch((e) =>
                            setDownloadError(e.message),
                          )
                        }
                      >
                        <ArrowDown size={15} /> Download DXF
                      </button>
                    )}
                    {reviewPdf && (
                      <button
                        type="button"
                        onClick={() =>
                          void download(reviewPdf.url, reviewPdf.name).catch(
                            (e) => setDownloadError(e.message),
                          )
                        }
                      >
                        <ArrowDown size={15} /> Review PDF
                      </button>
                    )}
                    {reviewPackage && (
                      <button
                        type="button"
                        onClick={() =>
                          void download(
                            reviewPackage.url,
                            reviewPackage.name,
                          ).catch((e) => setDownloadError(e.message))
                        }
                      >
                        <ArrowDown size={15} /> Review package
                      </button>
                    )}
                    {!dxf &&
                      !reviewPackage &&
                      run.artifacts.map((artifact) => (
                        <button
                          key={artifact.sha256 || artifact.name}
                          type="button"
                          onClick={() =>
                            void download(artifact.url, artifact.name).catch(
                              (e) => setDownloadError(e.message),
                            )
                          }
                        >
                          <ArrowDown size={15} /> {artifact.name}
                        </button>
                      ))}
                  </div>
                  {downloadError && (
                    <p className="drr-inline-error" role="alert">
                      {downloadError}
                    </p>
                  )}
                </div>
              )}
              {screen !== "requests" && selectedChange && (
                <section
                  className="drr-viewer"
                  aria-label="Saved drawing changes"
                >
                  <div className="drr-viewer-heading">
                    <div>
                      <span className="drr-eyebrow">Drawing comparison</span>
                      <h3>{selectedChange.label}</h3>
                    </div>
                    <div className="drr-viewer-nav">
                      <button
                        type="button"
                        aria-label="Previous drawing change"
                        disabled={changeIndex <= 0}
                        onClick={() => setChangeIndex((i) => i - 1)}
                      >
                        <ArrowLeft size={17} />
                      </button>
                      <span>
                        {Math.min(changeIndex + 1, run!.changes.length)} /{" "}
                        {run!.changes.length}
                      </span>
                      <button
                        type="button"
                        aria-label="Next drawing change"
                        disabled={changeIndex >= run!.changes.length - 1}
                        onClick={() => setChangeIndex((i) => i + 1)}
                      >
                        <ArrowRight size={17} />
                      </button>
                    </div>
                  </div>
                  {selectedComment && (
                    <blockquote className="drr-viewer-request">
                      {selectedComment.text}
                    </blockquote>
                  )}
                  <Comparison change={selectedChange} />
                  <p className="drr-viewer-caption">
                    {selectedChange.verified
                      ? "Saved text re-read and untouched drawing bytes checked."
                      : "This change has not passed its saved-file checks."}{" "}
                    These are located CAD excerpts, not a full drawing render.
                    Open the DXF in your CAD app to inspect the complete
                    drawing.
                  </p>
                </section>
              )}
              {screen !== "changes" && run.comments.length > 0 && (
                <section
                  className="drr-request-list"
                  aria-label="Customer request register"
                >
                  <div
                    className="drr-register-toolbar"
                    role="group"
                    aria-label="Filter customer requests"
                  >
                    {(
                      [
                        ["all", "All requests", run.comments.length],
                        [
                          "open",
                          "Needs input",
                          run.comments.filter(
                            (c) => c.status === "needs_information",
                          ).length,
                        ],
                        [
                          "edits",
                          "With edits",
                          run.comments.filter((c) => changesByComment.has(c.id))
                            .length,
                        ],
                      ] as const
                    ).map(([key, label, count]) => (
                      <button
                        key={key}
                        type="button"
                        aria-pressed={requestFilter === key}
                        onClick={() => setRequestFilter(key)}
                      >
                        {label}
                        <span>{count}</span>
                      </button>
                    ))}
                  </div>
                  <div className="drr-request-columns" aria-hidden="true">
                    <span>Ref.</span>
                    <span>Customer request</span>
                    <span>Outcome</span>
                  </div>
                  <div className="drr-comments">
                    {run.comments.map((comment, index) => {
                      const changes = changesByComment.get(comment.id) || [];
                      if (
                        requestFilter === "open" &&
                        comment.status !== "needs_information"
                      )
                        return null;
                      if (requestFilter === "edits" && !changes.length)
                        return null;
                      const state =
                        changes.length && comment.status === "needs_information"
                          ? "Partly applied"
                          : comment.status === "needs_information"
                            ? "Needs input"
                            : commentStatus(comment.status);
                      return (
                        <details className="drr-request-row" key={comment.id}>
                          <summary>
                            <span className="drr-request-ref">
                              {String(index + 1).padStart(2, "0")}
                            </span>
                            <span
                              className="drr-request-title"
                              title={comment.text}
                            >
                              {comment.text}
                            </span>
                            <span
                              className={`drr-request-state is-${comment.status}`}
                            >
                              <span />
                              {state}
                            </span>
                            <CaretDown
                              size={14}
                              className="drr-request-chevron"
                            />
                          </summary>
                          <div className="drr-request-detail">
                            <blockquote>{comment.text}</blockquote>
                            <p>{comment.explanation}</p>
                            <div className="drr-request-actions">
                              {onOpenSource ? (
                                <button
                                  type="button"
                                  onClick={() =>
                                    onOpenSource(
                                      comment.source_document_id,
                                      comment.page,
                                    )
                                  }
                                  title={comment.source_name}
                                >
                                  <FileText size={15} />
                                  {/spec/i.test(comment.source_name || "")
                                    ? "Specification"
                                    : "Drawing"}{" "}
                                  · p. {comment.page ?? "—"}
                                  <ArrowSquareOut size={13} />
                                </button>
                              ) : (
                                <span className="drr-source-label">
                                  {comment.source_name || "Source document"} ·
                                  p. {comment.page ?? "—"}
                                </span>
                              )}
                              {!!changes.length && (
                                <button
                                  className="drr-jump"
                                  type="button"
                                  onClick={() => {
                                    if (
                                      screen === "requests" &&
                                      onViewChanges
                                    ) {
                                      onViewChanges(changes[0].id);
                                      return;
                                    }
                                    setChangeIndex(
                                      run.changes.findIndex(
                                        (c) => c.id === changes[0].id,
                                      ),
                                    );
                                    document
                                      .querySelector(".drr-viewer")
                                      ?.scrollIntoView({
                                        block: "center",
                                        behavior: "smooth",
                                      });
                                  }}
                                >
                                  <Check size={14} />
                                  View {changes.length} saved edits
                                  <ArrowSquareOut size={13} />
                                </button>
                              )}
                            </div>
                          </div>
                        </details>
                      );
                    })}
                  </div>
                  {requestFilter === "edits" && !run.changes.length && (
                    <p className="drr-filter-empty">
                      No saved edits in this review. Original requests remain in
                      All requests.
                    </p>
                  )}
                </section>
              )}
              {screen !== "requests" && run.remaining_issues.length > 0 && (
                <details className="drr-issues">
                  <summary>
                    Engineering limitations{" "}
                    <span>{run.remaining_issues.length} notes</span>
                    <CaretDown size={14} />
                  </summary>
                  <ul>
                    {run.remaining_issues.map((issue, index) => (
                      <li key={`${index}-${issue}`}>{issue}</li>
                    ))}
                  </ul>
                </details>
              )}
              {screen !== "requests" && run.status === "ready_for_review" && (
                <div className="drr-decision">
                  <label htmlFor={`drr-note-${id}`}>
                    Engineer note{" "}
                    <span>
                      Optional for approval; recommended when requesting
                      changes.
                    </span>
                  </label>
                  <textarea
                    id={`drr-note-${id}`}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    maxLength={2000}
                    placeholder="Record review findings or the changes needed…"
                  />
                  <div className="drr-decision-actions">
                    <button
                      type="button"
                      className="drr-request"
                      disabled={!!busy || !run.current}
                      onClick={() => void decide("request_changes")}
                    >
                      {busy === "request" ? "Submitting…" : "Request changes"}
                    </button>
                    <button
                      type="button"
                      className="drr-approve"
                      disabled={!!busy || !canApprove}
                      title={
                        !canApprove
                          ? "The run must be current, verified, and clear of remaining issues."
                          : undefined
                      }
                      onClick={() => void decide("approve")}
                    >
                      {busy === "approve" ? "Approving…" : "Approve revision"}
                    </button>
                  </div>
                  {!canApprove && (
                    <p className="drr-approval-note">
                      Approval is available when this current revision is
                      eligible and has no remaining issues.
                    </p>
                  )}
                </div>
              )}
              {screen !== "requests" &&
                (run.status === "needs_information" ||
                  run.totals.needs_information > 0) && (
                  <p className="drr-honest-note">
                    This package is partial. Resolve the listed information
                    requests before engineering approval.
                  </p>
                )}
            </>
          )}
          {!run && view.source_ready && screen !== "requests" && (
            <div className="drr-empty">
              <p>
                Use the actual drawing comments attached to this order to
                prepare a change review.
              </p>
              <button
                type="button"
                disabled={!!busy}
                onClick={() => void prepare()}
              >
                {busy === "prepare" ? (
                  <>
                    <CircleNotch size={16} className="drr-spin" /> Preparing…
                  </>
                ) : (
                  "Prepare from drawing comments"
                )}
              </button>
            </div>
          )}
          {canPrepare && run && screen !== "requests" && (
            <div className="drr-reprepare">
              <button
                type="button"
                disabled={!!busy}
                onClick={() => void prepare()}
              >
                {busy === "prepare" ? "Preparing…" : "Prepare a new review"}
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
