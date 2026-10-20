type SourceRecord = {
  documents: { id: string }[];
  sources: { id: string; document_id: string }[];
  comments: {
    id: string;
    document_id: string;
    source_ids: string[];
    target_source_id: string;
  }[];
};

/** Restore document reading order when storage returns spans in UUID order. */
export function sortDocumentSources<
  T extends {
    location: { page?: number; line?: number | string; cells?: string };
  },
>(sources: T[]): T[] {
  const collator = new Intl.Collator("en", {
    numeric: true,
    sensitivity: "base",
  });
  return sources
    .map((source, index) => ({ source, index }))
    .sort((a, b) => {
      const page =
        (a.source.location.page ?? 0) - (b.source.location.page ?? 0);
      if (page) return page;
      const left = String(
        a.source.location.cells ?? a.source.location.line ?? "",
      );
      const right = String(
        b.source.location.cells ?? b.source.location.line ?? "",
      );
      if (!left || !right) return left ? -1 : right ? 1 : a.index - b.index;
      return collator.compare(left, right) || a.index - b.index;
    })
    .map(({ source }) => source);
}

/** Resolve only evidence that belongs to this record; never substitute another source. */
export function resolveRecordSource(
  record: SourceRecord,
  sourceId: string,
  requestedComment?: string | null,
) {
  const source = record.sources.find((item) => item.id === sourceId);
  const document = record.documents.find(
    (item) => item.id === (source?.document_id || sourceId),
  );
  if (!document) return null;
  const associated = record.comments.filter((comment) =>
    source
      ? comment.source_ids.includes(source.id) ||
        comment.target_source_id === source.id
      : comment.document_id === document.id,
  );
  const comment =
    associated.find((item) => item.id === requestedComment) ||
    (source ? associated[0] : undefined);
  return { sourceId, documentId: document.id, commentId: comment?.id || null };
}
