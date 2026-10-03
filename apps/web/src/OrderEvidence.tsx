import { authorizationHeaders } from "./api";
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
export function OrderEvidence({
  document,
  sources,
  selectedId,
  close,
  contentUrl,
}: {
  document: OrderDocument;
  sources: OrderSource[];
  selectedId?: string;
  close: () => void;
  contentUrl?: string;
}) {
  const selected = sources.find((s) => s.id === selectedId);
  const [page, setPage] = useState(Number(selected?.location.page ?? 1));
  const [pages, setPages] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const pdf = document.name.toLowerCase().endsWith(".pdf");
  useEffect(() => {
    if (!pdf) return;
    let disposed = false;
    let loadingTask: any;
    let renderTask: any;
    setLoading(true);
    setError("");
    void (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        loadingTask = pdfjs.getDocument({
          httpHeaders: await authorizationHeaders(),
          url: contentUrl || `/api/documents/${document.id}/content`,
        });
        const doc = await loadingTask.promise;
        if (disposed) return;
        setPages(doc.numPages);
        const sheet = await doc.getPage(page);
        const viewport = sheet.getViewport({ scale: 1.4 });
        if (canvas.current && !disposed) {
          canvas.current.width = viewport.width;
          canvas.current.height = viewport.height;
          renderTask = sheet.render({ canvas: canvas.current, viewport });
          await renderTask.promise;
        }
      } catch (err) {
        if (!disposed) setError((err as Error).message);
      } finally {
        if (!disposed) setLoading(false);
      }
    })();
    return () => {
      disposed = true;
      renderTask?.cancel();
      loadingTask?.destroy();
    };
  }, [document.id, pdf, page, contentUrl]);
  const bbox = selected?.location.bbox;
  return (
    <Modal
      title={document.name}
      eyebrow={document.role?.replaceAll("_", " ") || "Source evidence"}
      onClose={close}
      wide
    >
      <div className="ow-source-toolbar">
        <span>{document.revision_label || "Original source"}</span>
        <a
          className="text-button"
          href={contentUrl || `/api/documents/${document.id}/content`}
          target="_blank"
          rel="noreferrer"
        >
          Open original <ArrowUpRight size={15} />
        </a>
      </div>
      {document.public_source && (
        <div className="record-source-origin">
          <a href={document.public_source.url} target="_blank" rel="noreferrer">
            Publisher’s original PDF <ArrowUpRight size={13} />
          </a>
          <span>
            Excerpt p. {page} = original p.{" "}
            {document.public_source.pages[page - 1] ?? "—"}. Original annotation
            objects retained.
          </span>
        </div>
      )}
      {document.coverage.filter((part) =>
        ["failed", "scanned", "unsupported"].includes(part.state || ""),
      ).length > 0 && (
        <div className="ow-command-note" role="note">
          {document.coverage
            .filter((part) =>
              ["failed", "scanned", "unsupported"].includes(part.state || ""),
            )
            .map((part, index) => (
              <p key={index}>
                {part.label}: {part.detail || "Manual review required."}
              </p>
            ))}
        </div>
      )}
      {selected && (
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
      {error && <ErrorNote message={error} />}{" "}
      {pdf ? (
        <>
          <div className="ow-page-controls">
            <button
              className="icon-button"
              aria-label="Previous page"
              onClick={() => setPage((p) => p - 1)}
              disabled={page <= 1}
            >
              <CaretLeft />
            </button>
            <span>
              Page {page} of {pages}
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
              disabled={zoom <= 100}
              onClick={() => setZoom((z) => z - 50)}
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
              onClick={() => setZoom((z) => z + 50)}
            >
              <MagnifyingGlassPlus />
            </button>
          </div>
          <div className="ow-pdf">
            <div className="ow-pdf-sheet" style={{ width: `${zoom}%` }}>
              <canvas ref={canvas} />
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
            <div key={s.id} className={s.id === selectedId ? "selected" : ""}>
              <span>{s.location.cells || s.location.line || "Excerpt"}</span>
              <p>{s.text}</p>
            </div>
          ))}
          {!sources.length && (
            <Empty title="This source is still being read">
              Extracted text will appear here when processing finishes.
            </Empty>
          )}
        </div>
      )}
    </Modal>
  );
}
