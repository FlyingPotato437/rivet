import { apiUrl, authorizationHeaders } from "./api";
import { useEffect, useRef, useState } from "react";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  ArrowUpRight,
  FileText,
  CaretLeft,
  CaretRight,
  MagnifyingGlassPlus,
  MagnifyingGlassMinus,
} from "@phosphor-icons/react";
import { Busy, Empty, ErrorNote, Modal } from "./ui";
import type { OrderDocument, OrderSource } from "./order-types";

export function EvidenceLinks({
  ids,
  sources,
  documents,
  open,
}: {
  ids: string[];
  sources: OrderSource[];
  documents: OrderDocument[];
  open: (id: string) => void;
}) {
  return (
    <div className="ow-evidence-links">
      {[...new Set(ids)].map((id) => {
        const source = sources.find((s) => s.id === id);
        const document = documents.find((d) => d.id === source?.document_id);
        return (
          <button
            key={id}
            className="ow-evidence-link"
            onClick={() => open(id)}
          >
            <FileText size={13} />
            <span>{document?.name ?? "Source evidence"}</span>
            {source?.location.page && <small>p. {source.location.page}</small>}
            <ArrowUpRight size={12} />
          </button>
        );
      })}
    </div>
  );
}
export type EvidencePosition = {
  page: number;
  zoom: number;
  scrollTop: number;
  scrollLeft: number;
};

export function OrderEvidence({
  document,
  sources,
  selectedId,
  selectionRequest,
  close,
  contentUrl,
  sharedExcerpts = false,
  inline = false,
  workbench = false,
  initialPosition,
  onPositionChange,
}: {
  document: OrderDocument;
  sources: OrderSource[];
  selectedId?: string;
  selectionRequest?: number;
  close: () => void;
  contentUrl?: string;
  sharedExcerpts?: boolean;
  inline?: boolean;
  workbench?: boolean;
  initialPosition?: EvidencePosition;
  onPositionChange?: (position: EvidencePosition) => void;
}) {
  const selected = sources.find((s) => s.id === selectedId);
  const [page, setPage] = useState(
    Number(selected?.location.page ?? initialPosition?.page ?? 1),
  );
  const [pages, setPages] = useState(1);
  const [zoom, setZoom] = useState(initialPosition?.zoom ?? 100);
  const [pdfDocument, setPdfDocument] = useState<any>(null);
  const scrollArea = useRef<HTMLDivElement>(null);
  const selectedText = useRef<HTMLDivElement>(null);
  const positionCallback = useRef(onPositionChange);
  positionCallback.current = onPositionChange;
  const restorePosition = useRef(initialPosition);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const pdf = document.name.toLowerCase().endsWith(".pdf");
  useEffect(() => {
    if (!pdf && selectedId)
      selectedText.current?.scrollIntoView({ block: "nearest" });
  }, [pdf, selectedId, selectionRequest, selected?.id]);
  useEffect(() => {
    if (!selected?.location.page) return;
    if (
      selectionRequest ||
      restorePosition.current?.page !== Number(selected.location.page)
    )
      restorePosition.current = undefined;
    setPage(Number(selected.location.page));
    scrollArea.current?.scrollTo({ top: 0, left: 0 });
  }, [selectedId, selected?.location.page, selectionRequest]);
  useEffect(() => {
    if (!pdf) return;
    let disposed = false;
    let loadingTask: any;
    setLoading(true);
    setPdfDocument(null);
    setError("");
    void (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        if (disposed) return;
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        const httpHeaders = await authorizationHeaders();
        if (disposed) return;
        loadingTask = pdfjs.getDocument({
          httpHeaders,
          url: apiUrl(contentUrl || `/api/documents/${document.id}/content`),
        });
        const loaded = await loadingTask.promise;
        if (disposed) return;
        setPages(loaded.numPages);
        setPage((current) => Math.max(1, Math.min(current, loaded.numPages)));
        setPdfDocument(loaded);
      } catch (err) {
        if (!disposed) {
          setError((err as Error).message);
          setLoading(false);
        }
      }
    })();
    return () => {
      disposed = true;
      loadingTask?.destroy();
    };
  }, [document.id, pdf, contentUrl]);
  useEffect(() => {
    if (!pdfDocument) return;
    let disposed = false;
    let renderTask: any;
    setLoading(true);
    setError("");
    void (async () => {
      try {
        const sheet = await pdfDocument.getPage(page);
        if (disposed || !canvas.current) return;
        const viewport = sheet.getViewport({ scale: 1.5 });
        canvas.current.width = viewport.width;
        canvas.current.height = viewport.height;
        renderTask = sheet.render({ canvas: canvas.current, viewport });
        await renderTask.promise;
        if (!disposed && restorePosition.current && scrollArea.current) {
          scrollArea.current.scrollTop = restorePosition.current.scrollTop;
          scrollArea.current.scrollLeft = restorePosition.current.scrollLeft;
          restorePosition.current = undefined;
        }
      } catch (err) {
        if (!disposed && (err as Error).name !== "RenderingCancelledException")
          setError((err as Error).message);
      } finally {
        if (!disposed) setLoading(false);
      }
    })();
    return () => {
      disposed = true;
      renderTask?.cancel();
    };
  }, [pdfDocument, page]);
  useEffect(() => {
    positionCallback.current?.({
      page,
      zoom,
      scrollTop: scrollArea.current?.scrollTop ?? 0,
      scrollLeft: scrollArea.current?.scrollLeft ?? 0,
    });
  }, [page, zoom]);
  const bbox = selected?.location.bbox;
  const content = (
    <>
      <div
        className={
          "ow-source-toolbar" +
          (workbench && !pdf ? " workbench-text-toolbar" : "")
        }
      >
        <span>
          {sharedExcerpts
            ? "Shared excerpts"
            : document.revision_label || "Original source"}
        </span>
        <a
          className="text-button"
          href={contentUrl || `/api/documents/${document.id}/content`}
          target="_blank"
          rel="noreferrer"
        >
          {sharedExcerpts ? "Download shared excerpts" : "Open original"}{" "}
          <ArrowUpRight size={15} />
        </a>
      </div>
      {!!document.warnings?.length && (
        <details className="record-document-warnings">
          <summary>
            {document.warnings.length} source{" "}
            {document.warnings.length === 1 ? "warning" : "warnings"} · review
            required
          </summary>
          {document.warnings.map((warning, index) => (
            <p key={index}>{warning}</p>
          ))}
        </details>
      )}
      {document.public_source && (
        <details
          className="record-source-origin"
          open={!workbench || undefined}
        >
          <summary>
            Source provenance · excerpt p. {page} / original p.{" "}
            {document.public_source.pages[page - 1] ?? "—"}
          </summary>
          <a href={document.public_source.url} target="_blank" rel="noreferrer">
            Publisher’s original PDF <ArrowUpRight size={13} />
          </a>
          <span>
            Excerpt p. {page} = original p.{" "}
            {document.public_source.pages[page - 1] ?? "—"}. Original annotation
            objects retained.
          </span>
        </details>
      )}
      {document.coverage.filter((part) =>
        ["failed", "scanned", "unsupported"].includes(part.state || ""),
      ).length > 0 && (
        <details className="ow-command-note" open={!workbench || undefined}>
          <summary>Some source areas need manual review</summary>
          {document.coverage
            .filter((part) =>
              ["failed", "scanned", "unsupported"].includes(part.state || ""),
            )
            .map((part, index) => (
              <p key={index}>
                {part.label}: {part.detail || "Manual review required."}
              </p>
            ))}
        </details>
      )}
      {selected && !inline && pdf && (
        <blockquote className="ow-citation">
          <span>
            {selected.location.page
              ? `Page ${selected.location.page}`
              : selected.location.cells ||
                `Source ${selected.location.line || "excerpt"}`}
          </span>
          <p>
            {selected.text ||
              "No readable text in this area. Review the original below."}
          </p>
        </blockquote>
      )}
      {selected && inline && pdf && (
        <details className="record-inline-excerpt">
          <summary>
            Selected excerpt ·{" "}
            {selected.location.page
              ? `page ${selected.location.page}`
              : selected.location.cells ||
                `line ${selected.location.line || "—"}`}
          </summary>
          <p>
            {selected.text ||
              "No readable text in this area. Review the original below."}
          </p>
        </details>
      )}
      {error && <ErrorNote message={error} />}{" "}
      {pdf ? (
        <>
          <div className="ow-page-controls">
            {workbench && (
              <a
                className="workbench-original-link"
                href={contentUrl || `/api/documents/${document.id}/content`}
                target="_blank"
                rel="noreferrer"
              >
                Original
                <ArrowUpRight size={13} />
              </a>
            )}
            <button
              className="icon-button"
              aria-label="Previous page"
              onClick={() => setPage((p) => p - 1)}
              disabled={page <= 1}
            >
              <CaretLeft />
            </button>
            <span>
              <input
                type="number"
                min={1}
                max={pages}
                value={page}
                aria-label="Page number"
                onChange={(event) => {
                  const next = Number(event.target.value);
                  if (Number.isInteger(next) && next >= 1 && next <= pages)
                    setPage(next);
                }}
              />{" "}
              <span>of {pages}</span>
            </span>
            <button
              className="icon-button"
              aria-label="Next page"
              onClick={() => setPage((p) => p + 1)}
              disabled={page >= pages}
            >
              <CaretRight />
            </button>
            {loading && <Busy text="Reading page…" />}
            <button
              className="icon-button"
              aria-label="Zoom out"
              disabled={zoom <= 75}
              onClick={() => setZoom((z) => Math.max(75, z - 25))}
            >
              <MagnifyingGlassMinus />
            </button>
            <button
              className="text-button"
              aria-label="Fit page"
              onClick={() => setZoom(100)}
            >
              {zoom}%
            </button>
            <button
              className="icon-button"
              aria-label="Zoom in"
              disabled={zoom >= 300}
              onClick={() => setZoom((z) => Math.min(300, z + 25))}
            >
              <MagnifyingGlassPlus />
            </button>
          </div>
          <div
            className="ow-pdf"
            ref={scrollArea}
            onScroll={() =>
              positionCallback.current?.({
                page,
                zoom,
                scrollTop: scrollArea.current?.scrollTop ?? 0,
                scrollLeft: scrollArea.current?.scrollLeft ?? 0,
              })
            }
          >
            <div className="ow-pdf-sheet" style={{ width: `${zoom}%` }}>
              <canvas
                ref={canvas}
                aria-label={`${document.name}, page ${page}`}
              />
              {bbox && bbox.length === 4 && selected.location.page === page && (
                <div
                  className="ow-region"
                  style={{
                    left: `${bbox[0] * 100}%`,
                    top: `${bbox[1] * 100}%`,
                    width: `${(bbox[2] - bbox[0]) * 100}%`,
                    height: `${(bbox[3] - bbox[1]) * 100}%`,
                  }}
                />
              )}
            </div>
          </div>
        </>
      ) : (
        <div className="ow-source-text">
          {sources.map((s) => (
            <div
              key={s.id}
              ref={s.id === selectedId ? selectedText : undefined}
              className={s.id === selectedId ? "selected" : ""}
            >
              <span>{s.location.cells || s.location.line || "Excerpt"}</span>
              <p>{s.text}</p>
            </div>
          ))}
          {!sources.length && (
            <Empty
              title={
                document.state === "queued"
                  ? "This source is still being read"
                  : document.state === "failed"
                    ? "This source could not be read"
                    : "No readable text was extracted"
              }
            >
              {document.state === "queued"
                ? "Extracted text will appear here when processing finishes."
                : "Open the original file and review it directly. The source has been retained."}
            </Empty>
          )}
        </div>
      )}
    </>
  );
  if (inline) {
    return (
      <section
        className={
          "record-inline-evidence" + (workbench ? " workbench-evidence" : "")
        }
        aria-label="Source document"
      >
        {!workbench && (
          <header className="record-inline-evidence-head">
            <button className="text-button" onClick={close}>
              <CaretLeft size={16} /> Back to record
            </button>
            <div>
              <span>
                {document.role?.replaceAll("_", " ") || "Source evidence"}
              </span>
              <h2>{document.name}</h2>
            </div>
          </header>
        )}
        {content}
      </section>
    );
  }
  return (
    <Modal
      title={document.name}
      eyebrow={document.role?.replaceAll("_", " ") || "Source evidence"}
      onClose={close}
      wide
    >
      {content}
    </Modal>
  );
}
