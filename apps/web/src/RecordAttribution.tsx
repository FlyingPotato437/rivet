import { ArrowsClockwise, Sparkle, User } from "@phosphor-icons/react";
import type { RecordComment } from "./record-types";

export type ActorKind = "ai" | "automatic" | "human" | "unknown";

export function Attribution({
  kind,
  children,
  title,
}: {
  kind: ActorKind;
  children: React.ReactNode;
  title?: string;
}) {
  const Icon =
    kind === "ai" ? Sparkle : kind === "automatic" ? ArrowsClockwise : User;
  return (
    <span className={`record-attribution is-${kind}`} title={title}>
      <Icon size={13} aria-hidden="true" />
      {children}
    </span>
  );
}

export function CommentAttribution({
  comment,
  compact = false,
}: {
  comment: Partial<RecordComment>;
  compact?: boolean;
}) {
  const origins: Record<string, string> = {
    annotation: "Imported from PDF markup",
    email: "Imported from email",
    "numbered text": "Imported from document text",
    "unreadable area": "Automatic flag · transcription needed",
  };
  if (comment.origin === "manual")
    return <Attribution kind="human">Added manually</Attribution>;
  const label = origins[comment.origin || ""];
  if (!label) return null;
  return (
    <>
      <Attribution kind="automatic" title={label}>
        {compact ? "Imported" : label}
      </Attribution>
      {comment.original_text && comment.text !== comment.original_text && (
        <Attribution kind="human">
          {compact ? "Edited" : "Text edited by team"}
        </Attribution>
      )}
    </>
  );
}

export function ActivityAttribution({
  actor,
  kind,
}: {
  actor: string;
  kind?: ActorKind;
}) {
  // Older records predate explicit actor kinds. These are the two reserved
  // system actors used by the existing import and coordination engines.
  const source =
    kind ??
    (actor === "Rivet" || actor === "Document reader"
      ? "automatic"
      : actor
        ? "human"
        : "unknown");
  return (
    <Attribution kind={source}>
      {source === "ai"
        ? "AI generated"
        : source === "automatic"
          ? "Automatic · Rivet"
          : source === "human"
            ? `By ${actor}`
            : "Author not recorded"}
    </Attribution>
  );
}
