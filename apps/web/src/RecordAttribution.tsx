import { ArrowsClockwise, Sparkle, User } from "@phosphor-icons/react";
import type { RecordComment } from "./record-types";
import { useAccount } from "./Auth";

export function ActorName({ actor }: { actor?: string }) {
  const account = useAccount();
  const identifier = actor?.trim() || "";
  const technicalId = /^(?:user_|org_|[0-9a-f]{8}-[0-9a-f-]{27,})/i.test(
    identifier,
  );
  const label =
    identifier && identifier === account.userId
      ? account.name || "Team member"
      : technicalId || !identifier
        ? "Team member"
        : identifier;
  return (
    <span title={identifier !== label ? identifier : undefined}>{label}</span>
  );
}

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
  proposalOrigin,
}: {
  actor: string;
  kind?: ActorKind;
  proposalOrigin?: "ai" | "rules";
}) {
  if (proposalOrigin)
    return (
      <>
        <Attribution kind={proposalOrigin === "ai" ? "ai" : "automatic"}>
          {proposalOrigin === "ai" ? "AI prepared" : "Rule-based preparation"}
        </Attribution>
        <Attribution kind="human">
          Accepted by <ActorName actor={actor} />
        </Attribution>
      </>
    );
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
      {source === "ai" ? (
        "AI generated"
      ) : source === "automatic" ? (
        "Automatic · Rivet"
      ) : source === "human" ? (
        <>
          By <ActorName actor={actor} />
        </>
      ) : (
        "Author not recorded"
      )}
    </Attribution>
  );
}
