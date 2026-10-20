import type { ProjectStatus } from "./project-flow";

export type StatusKind = "need" | "working" | "done" | "missing" | "waiting";

/** One cool colour family: lavender needs you, periwinkle working, sage done, rose missing, grey waiting. */
export function StatusIcon({
  kind,
  size = 15,
  title,
}: {
  kind: StatusKind;
  size?: number;
  title?: string;
}) {
  return (
    <svg
      className={`status-icon is-${kind}`}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      {kind === "need" ? (
        <>
          <circle
            cx="8"
            cy="8"
            r="6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
          />
          <path
            d="M8 4.5v4"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
          <circle cx="8" cy="11" r="1" fill="currentColor" />
        </>
      ) : kind === "working" ? (
        <>
          <circle
            cx="8"
            cy="8"
            r="6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
          />
          <path d="M8 4a4 4 0 0 1 0 8z" fill="currentColor" />
        </>
      ) : kind === "done" ? (
        <>
          <circle cx="8" cy="8" r="7" fill="currentColor" />
          <path
            d="M5 8.2l2 2 4-4.2"
            fill="none"
            stroke="var(--rv-bg)"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </>
      ) : kind === "missing" ? (
        <>
          <circle cx="8" cy="8" r="7" fill="currentColor" />
          <path
            d="M5.5 5.5l5 5M10.5 5.5l-5 5"
            stroke="var(--rv-bg)"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </>
      ) : (
        <circle
          cx="8"
          cy="8"
          r="6"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeDasharray="2.2 2"
        />
      )}
    </svg>
  );
}

export function statusKind(status: ProjectStatus | string): StatusKind {
  if (status === "needs_you") return "need";
  if (status === "handling") return "working";
  if (status === "ready" || status === "prepared" || status === "up_to_date")
    return "done";
  return "waiting";
}

export function initials(name = "") {
  const words = name
    .replace(/[^A-Za-z0-9 ]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  return (
    words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "?").slice(0, 2)
  ).toUpperCase();
}

export function Monogram({ name }: { name: string }) {
  return (
    <span className="monogram" aria-hidden>
      {initials(name)}
    </span>
  );
}

export function CustomerChip({ name }: { name?: string }) {
  return name ? (
    <span className="customer-chip" title={name}>
      <Monogram name={name} />
      <span>{name}</span>
    </span>
  ) : (
    <span className="customer-chip is-empty">—</span>
  );
}

const rivetPath =
  "M5 5h14.5v6H11v6h8.5v10H5V5Zm14.5 6H27v6h-7.5V11Zm0 6H25l4 10h-7l-2.5-10Z";

/** The Rivet mark used as an avatar for work Rivet did. */
export function RivetAvatar() {
  return (
    <span className="rivet-avatar" aria-label="Rivet">
      <svg width="11" height="11" viewBox="0 0 32 32" aria-hidden>
        <path d={rivetPath} fill="currentColor" />
      </svg>
    </span>
  );
}

/** A small one-line diagram; a dashed revision cloud marks orders waiting on a decision. */
export function BlueprintThumb({ cloud = false }: { cloud?: boolean }) {
  return (
    <span className="blueprint-thumb" aria-hidden>
      <svg viewBox="0 0 44 30">
        <g stroke="currentColor" strokeWidth="1" fill="none">
          <path d="M22 3v6M14 9h16M14 9v6M22 9v6M30 9v6" />
          <rect x="11" y="15" width="6" height="5" />
          <rect x="19" y="15" width="6" height="5" />
          <rect x="27" y="15" width="6" height="5" />
          <path d="M14 20v6M22 20v6M30 20v6" />
        </g>
        {cloud && (
          <circle
            className="revision-cloud"
            cx="31"
            cy="17"
            r="5"
            fill="none"
            strokeWidth="1"
            strokeDasharray="2 1.4"
          />
        )}
      </svg>
    </span>
  );
}
