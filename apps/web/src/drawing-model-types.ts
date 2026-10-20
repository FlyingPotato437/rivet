export type DrawingSource = {
  document_id: string;
  name: string;
  revision: string;
  sha256: string;
};
export type DrawingInkEvidence = {
  source_id?: string | null;
  sha256?: string;
  page?: number;
  coordinate_system?: string;
  [key: string]: unknown;
};
export type DrawingConnection = {
  id: string;
  page: number;
  terminal_ids: string[];
  status?: string;
  segments: {
    points: [number, number][];
    evidence?: DrawingInkEvidence;
  }[];
  evidence?: DrawingInkEvidence[];
};
export type DrawingPage = {
  page: number;
  width_pt: number;
  height_pt: number;
  devices: { tag: string; bbox?: number[]; normalized_bbox?: number[] }[];
  connections: DrawingConnection[];
  issues?: { code?: string; reason: string }[];
  coverage?: {
    status?: string;
    complete_inventory_established?: boolean;
    unaccounted_vector_count?: number;
  };
};
export type VectorDrawing = {
  source: DrawingSource;
  side: "baseline" | "target";
  coordinate_system: string;
  physical_scale: null;
  pages: DrawingPage[];
  issues?: { code?: string; reason: string }[];
  coverage?: DrawingPage["coverage"];
};
export type DrawingEquipmentModel = {
  version: string;
  vector_drawings?: VectorDrawing[];
  pdf_revision_check?: {
    summary: {
      observed_geometry_changes: number;
      observed_connectivity_changes: number;
      unknown_changes: number;
      complete_revision_verified: false;
    };
  } | null;
};

export function drawingSourceKey(drawing: VectorDrawing) {
  return `${drawing.side}:${drawing.source.document_id}:${drawing.source.sha256}`;
}

export function selectVectorDrawing(drawings: VectorDrawing[], key: string) {
  return (
    drawings.find((drawing) => drawingSourceKey(drawing) === key) ||
    drawings.find((drawing) => drawing.side === "target") ||
    drawings[0]
  );
}

export function selectDrawingPage(
  drawing: VectorDrawing | undefined,
  number: number,
) {
  const pages = (drawing?.pages || []).filter(
    (page) =>
      Number.isSafeInteger(page.page) &&
      page.page > 0 &&
      Number.isFinite(page.width_pt) &&
      page.width_pt > 0 &&
      page.width_pt <= 50_000 &&
      Number.isFinite(page.height_pt) &&
      page.height_pt > 0 &&
      page.height_pt <= 50_000,
  );
  return pages.find((page) => page.page === number) || pages[0];
}

/** Page images always come from the owned order endpoint, never a model URL. */
export function drawingPageImagePath(
  orderId: string,
  documentId: string,
  page: number,
) {
  if (!orderId || !documentId || !Number.isSafeInteger(page) || page < 1)
    return null;
  const query = new URLSearchParams({
    document_id: documentId,
    page: String(page),
  });
  return `/api/orders/${encodeURIComponent(orderId)}/equipment/evidence?${query}`;
}

export function tracedDrawingConnections(page: DrawingPage | undefined) {
  const connections = page?.connections || [];
  const counts = new Map<string, number>();
  for (const connection of connections)
    counts.set(connection.id, (counts.get(connection.id) || 0) + 1);
  return connections.filter(
    (connection) =>
      typeof connection.id === "string" &&
      !!connection.id &&
      counts.get(connection.id) === 1 &&
      connection.page === page?.page &&
      Array.isArray(connection.terminal_ids) &&
      connection.terminal_ids.length >= 2 &&
      connection.terminal_ids.length <= 500 &&
      connection.terminal_ids.every(
        (terminal) => typeof terminal === "string" && !!terminal,
      ) &&
      new Set(connection.terminal_ids).size === connection.terminal_ids.length,
  );
}

/** Exact ink pieces only: no route is drawn between terminal names. */
export function drawingConnectionSegments(
  drawing: VectorDrawing,
  page: DrawingPage,
  connection: DrawingConnection,
) {
  if (
    drawing.coordinate_system !== "visible-page-points" ||
    drawing.physical_scale !== null ||
    connection.page !== page.page ||
    !Array.isArray(connection.segments) ||
    !connection.segments.length ||
    connection.segments.length > 12_000
  )
    return null;
  for (const segment of connection.segments) {
    const evidence = segment.evidence;
    if (
      !evidence ||
      evidence.source_id !== drawing.source.document_id ||
      evidence.sha256 !== drawing.source.sha256 ||
      evidence.page !== page.page ||
      evidence.coordinate_system !== "visible-page-points" ||
      !Array.isArray(segment.points) ||
      segment.points.length !== 2 ||
      segment.points.some(
        (point) =>
          !Array.isArray(point) ||
          point.length !== 2 ||
          !point.every(
            (coordinate) =>
              typeof coordinate === "number" && Number.isFinite(coordinate),
          ) ||
          point[0] < 0 ||
          point[0] > page.width_pt ||
          point[1] < 0 ||
          point[1] > page.height_pt,
      )
    )
      return null;
  }
  return connection.segments;
}

export function drawingCoverageLine(drawing: VectorDrawing, page: DrawingPage) {
  const incomplete =
    page.coverage?.status !== "bounded" ||
    drawing.coverage?.status !== "bounded";
  return incomplete
    ? "Some drawing content is unaccounted for. These traces do not verify the full circuit or physical dimensions."
    : "Supported visible ink is traced. Full circuit coverage and physical dimensions remain unverified.";
}
