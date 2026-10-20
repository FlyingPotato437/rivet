import { useEffect, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Minus, Plus } from "@phosphor-icons/react";
import { api, apiFetch } from "./api";
import { ErrorNote } from "./ui";
import {
  drawingConnectionSegments,
  drawingCoverageLine,
  drawingPageImagePath,
  drawingSourceKey,
  selectDrawingPage,
  selectVectorDrawing,
  tracedDrawingConnections,
  type DrawingEquipmentModel,
} from "./drawing-model-types";
import "./drawing-model-evidence.css";

type ImageState = {
  key: string;
  status: "loading" | "ready" | "failed";
  url?: string;
  error?: string;
};

/** Actual PDF-page evidence, loaded only when someone wants the detail. */
export function DrawingModelEvidence({ id }: { id: string }) {
  const titleId = useId();
  const [open, setOpen] = useState(false);
  const [sourceKey, setSourceKey] = useState("");
  const [pageNumber, setPageNumber] = useState(1);
  const [connectionId, setConnectionId] = useState("");
  const [zoom, setZoom] = useState(1);
  const [retry, setRetry] = useState(0);
  const [image, setImage] = useState<ImageState | null>(null);
  const [aligned, setAligned] = useState(false);
  const query = useQuery({
    queryKey: ["equipment-model", id],
    queryFn: () => api<DrawingEquipmentModel>(`/orders/${id}/equipment/model`),
    enabled: open && !!id,
    staleTime: 15_000,
    retry: false,
  });
  const drawings = query.data?.vector_drawings || [];
  const drawing = selectVectorDrawing(drawings, sourceKey);
  const page = selectDrawingPage(drawing, pageNumber);
  const connections = tracedDrawingConnections(page);
  const selected = connections.find(
    (connection) => connection.id === connectionId,
  );
  const segments =
    drawing && page && selected
      ? drawingConnectionSegments(drawing, page, selected)
      : null;
  const selectedKey =
    drawing && page ? `${drawingSourceKey(drawing)}:${page.page}` : "";
  const path =
    drawing && page
      ? drawingPageImagePath(id, drawing.source.document_id, page.page)
      : null;
  const revision = query.data?.pdf_revision_check?.summary;

  useEffect(() => {
    setSourceKey("");
    setPageNumber(1);
    setConnectionId("");
    setZoom(1);
    setRetry(0);
    setImage(null);
    setAligned(false);
  }, [id]);

  useEffect(() => {
    if (!open || !path || !selectedKey) return;
    let active = true;
    let blobUrl = "";
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    setImage({ key: selectedKey, status: "loading" });
    setAligned(false);
    void (async () => {
      try {
        const blob = await Promise.race([
          (async () => {
            const response = await apiFetch(path, {
              signal: controller.signal,
            });
            if (!response.ok) {
              let message =
                "The source page could not open. Check your sign-in and retry.";
              try {
                const data = await response.json();
                if (typeof data.detail === "string") message = data.detail;
              } catch {
                /* The retry action remains available for non-JSON errors. */
              }
              throw new Error(message);
            }
            if (
              !(response.headers.get("content-type") || "").startsWith(
                "image/png",
              )
            )
              throw new Error("The source did not return a PDF page image.");
            return response.blob();
          })(),
          new Promise<never>((_, reject) => {
            timer = setTimeout(() => {
              controller.abort();
              reject(
                new Error(
                  "The source page took too long to open. Check your sign-in and retry.",
                ),
              );
            }, 30_000);
          }),
        ]);
        if (!active) return;
        if (!blob.size || blob.size > 20 * 1024 * 1024)
          throw new Error(
            "The source page image is empty or too large to display.",
          );
        blobUrl = URL.createObjectURL(blob);
        setImage({ key: selectedKey, status: "ready", url: blobUrl });
      } catch (e) {
        if (active)
          setImage({
            key: selectedKey,
            status: "failed",
            error: (e as Error).message,
          });
      } finally {
        if (timer) clearTimeout(timer);
      }
    })();
    return () => {
      active = false;
      controller.abort();
      if (timer) clearTimeout(timer);
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [open, path, selectedKey, retry]);

  const currentImage = image?.key === selectedKey ? image : null;
  return (
    <details
      className="drawing-model-evidence"
      onToggle={(event) => {
        setOpen(event.currentTarget.open);
        if (!event.currentTarget.open) {
          setImage(null);
          setAligned(false);
        }
      }}
    >
      <summary>
        <span>Drawing evidence</span>
        <small>Original PDF pages and traced connections</small>
      </summary>
      {open && (
        <div className="drawing-evidence-content" aria-labelledby={titleId}>
          <h4 id={titleId}>Trace the source drawing</h4>
          {query.isPending && (
            <p className="drawing-evidence-status" role="status">
              Reading the drawing evidence…
            </p>
          )}
          {query.isError && (
            <>
              <ErrorNote message={(query.error as Error).message} />
              <button
                type="button"
                className="text-button"
                onClick={() => void query.refetch()}
              >
                Retry drawing evidence
              </button>
            </>
          )}
          {query.data && !drawings.length && (
            <p className="drawing-evidence-status">
              No supported vector PDF drawing is available in the selected
              revision. Original files remain available in the order.
            </p>
          )}
          {drawing && !page && (
            <p className="drawing-evidence-status">
              The source page dimensions are unavailable. Open the original file
              from the order; no replacement drawing is shown here.
            </p>
          )}
          {drawing && page && (
            <>
              <div className="drawing-evidence-toolbar">
                <div className="drawing-evidence-selectors">
                  {drawings.length > 1 ? (
                    <label>
                      Drawing
                      <select
                        value={drawingSourceKey(drawing)}
                        onChange={(event) => {
                          setSourceKey(event.target.value);
                          setPageNumber(1);
                          setConnectionId("");
                          setZoom(1);
                        }}
                      >
                        {drawings.map((item) => (
                          <option
                            key={drawingSourceKey(item)}
                            value={drawingSourceKey(item)}
                          >
                            {item.side === "target" ? "Current" : "Before"} ·
                            Rev {item.source.revision} · {item.source.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <div className="drawing-evidence-source">
                      <strong>{drawing.source.name}</strong>
                      <span>
                        {drawing.side === "target" ? "Current" : "Before"} ·
                        Revision {drawing.source.revision}
                      </span>
                    </div>
                  )}
                  {drawing.pages.length > 1 ? (
                    <label className="drawing-page-picker">
                      Page
                      <select
                        value={page.page}
                        onChange={(event) => {
                          setPageNumber(Number(event.target.value));
                          setConnectionId("");
                          setZoom(1);
                        }}
                      >
                        {drawing.pages.map((item) => (
                          <option key={item.page} value={item.page}>
                            Page {item.page}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <span className="drawing-evidence-page">
                      Page {page.page}
                    </span>
                  )}
                </div>
                <div
                  className="drawing-evidence-zoom"
                  role="group"
                  aria-label="PDF evidence zoom"
                >
                  <button
                    type="button"
                    aria-label="Zoom out PDF evidence"
                    onClick={() => setZoom(Math.max(1, zoom - 0.5))}
                    disabled={zoom <= 1}
                  >
                    <Minus size={14} />
                  </button>
                  <span>{Math.round(zoom * 100)}%</span>
                  <button
                    type="button"
                    aria-label="Zoom in PDF evidence"
                    onClick={() => setZoom(Math.min(3, zoom + 0.5))}
                    disabled={zoom >= 3}
                  >
                    <Plus size={14} />
                  </button>
                </div>
              </div>
              <div className="drawing-evidence-desk">
                <div className="drawing-evidence-page-column">
                  <div
                    className="drawing-evidence-canvas"
                    tabIndex={0}
                    aria-label="Original PDF page, scroll to inspect when zoomed"
                  >
                    {currentImage?.status === "failed" ? (
                      <div className="drawing-image-message">
                        <p role="alert">{currentImage.error}</p>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => setRetry((value) => value + 1)}
                        >
                          Retry source page
                        </button>
                      </div>
                    ) : currentImage?.status !== "ready" ||
                      !currentImage.url ? (
                      <p className="drawing-image-message" role="status">
                        Loading the original page…
                      </p>
                    ) : (
                      <div
                        className={`drawing-page-scale drawing-page-zoom-${zoom * 100}`}
                        style={{
                          aspectRatio: `${page.width_pt} / ${page.height_pt}`,
                        }}
                      >
                        <img
                          src={currentImage.url}
                          alt={`${drawing.source.name}, revision ${drawing.source.revision}, page ${page.page}`}
                          onLoad={(event) => {
                            const target = event.currentTarget;
                            const ratio =
                              target.naturalWidth / target.naturalHeight;
                            setAligned(
                              Math.abs(
                                ratio / (page.width_pt / page.height_pt) - 1,
                              ) < 0.015,
                            );
                          }}
                          onError={() =>
                            setImage({
                              key: selectedKey,
                              status: "failed",
                              error:
                                "The source page image could not be displayed. Retry the page.",
                            })
                          }
                        />
                        {aligned && segments && (
                          <svg
                            viewBox={`0 0 ${page.width_pt} ${page.height_pt}`}
                            preserveAspectRatio="xMidYMid meet"
                            className="drawing-ink-overlay"
                            role="img"
                            aria-label={`Actual source ink for terminals ${selected!.terminal_ids.join(", ")}`}
                          >
                            {segments.map((segment, index) => (
                              <polyline
                                key={index}
                                points={segment.points
                                  .map((point) => point.join(","))
                                  .join(" ")}
                                vectorEffect="non-scaling-stroke"
                              />
                            ))}
                          </svg>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="drawing-evidence-caption">
                    <span>Original source page · schematic PDF points</span>
                    {currentImage?.status === "ready" && currentImage.url && (
                      <a
                        href={currentImage.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open page image
                      </a>
                    )}
                  </div>
                </div>
                <div className="drawing-connection-panel">
                  <h5>Traced connections</h5>
                  <p>Select a connection to highlight its actual ink.</p>
                  {connections.length ? (
                    <div
                      className="drawing-connection-list"
                      role="group"
                      aria-label="Source-traced terminal networks"
                    >
                      {connections.map((connection) => (
                        <button
                          type="button"
                          key={connection.id}
                          aria-pressed={selected?.id === connection.id}
                          onClick={() =>
                            setConnectionId(
                              selected?.id === connection.id
                                ? ""
                                : connection.id,
                            )
                          }
                        >
                          <span>{connection.terminal_ids.join(" · ")}</span>
                          {connection.terminal_ids.length > 2 && (
                            <small>Branched terminal network</small>
                          )}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p>
                      No supported labelled terminal network was established on
                      this page.
                    </p>
                  )}
                  {selected && !segments && (
                    <p className="drawing-trace-note">
                      This connection’s exact page trace is unavailable. No
                      replacement route is drawn.
                    </p>
                  )}
                  {selected && currentImage?.status === "ready" && !aligned && (
                    <p className="drawing-trace-note">
                      Page dimensions are not aligned yet. The overlay stays
                      hidden.
                    </p>
                  )}
                </div>
              </div>
              <p className="drawing-coverage-line">
                {drawingCoverageLine(drawing, page)}
              </p>
              {revision && (
                <p className="drawing-revision-line">
                  Across the selected revisions:{" "}
                  {revision.observed_geometry_changes} schematic layout{" "}
                  {revision.observed_geometry_changes === 1
                    ? "change"
                    : "changes"}{" "}
                  · {revision.observed_connectivity_changes} connection{" "}
                  {revision.observed_connectivity_changes === 1
                    ? "difference"
                    : "differences"}
                  {revision.unknown_changes
                    ? ` · ${revision.unknown_changes} unresolved ${revision.unknown_changes === 1 ? "comparison" : "comparisons"}`
                    : ""}
                  . This is a bounded observation, not a complete revision
                  approval.
                </p>
              )}
            </>
          )}
        </div>
      )}
    </details>
  );
}
