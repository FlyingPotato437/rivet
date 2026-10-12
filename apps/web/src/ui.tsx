import { useEffect, useRef, type ReactNode, type SVGProps } from "react";
import {
  X,
  ArrowRight,
  Check,
  CheckCircle,
  Circle,
  Clock,
  XCircle,
  CircleHalf,
  WarningCircle,
  CircleNotch,
} from "@phosphor-icons/react";
export function Mark({ small = false }: { small?: boolean }) {
  return (
    <svg
      width={small ? 20 : 30}
      height={small ? 20 : 30}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M5 5h14.5v6H11v6h8.5v10H5V5Zm14.5 6H27v6h-7.5V11Zm0 6H25l4 10h-7l-2.5-10Z"
        fill="currentColor"
      />
    </svg>
  );
}
export type RivetIconName =
  | "drawing"
  | "comment"
  | "revision"
  | "evidence"
  | "agent"
  | "equipment"
  | "handoff"
  | "review"
  | "inbox";

/** Domain marks share a 20-unit grid; conventional controls keep familiar icons. */
export function RivetIcon({
  name,
  size = 20,
  className = "",
  ...props
}: SVGProps<SVGSVGElement> & { name: RivetIconName; size?: number }) {
  const paths: Record<RivetIconName, ReactNode> = {
    drawing: <path d="M4 2.5h8l4 4v11H4zM12 2.5v4h4M7 11h6M7 14h3" />,
    comment: <path d="M3 3.5h14v10H9l-4 3v-3H3zM6.5 7h7M6.5 10h4" />,
    revision: (
      <path d="M7 2.5h9v12H7zM4 5.5H2.5v12H12v-1.5M10 6h3M10 9h3M10 12h1" />
    ),
    evidence: <path d="M8 3H3v6h5M12 17h5v-6h-5M7 10l6-5M7 15l6-5" />,
    agent: (
      <>
        <path d="M3 3h6v6H3zM11 11h6v6h-6zM9 6h5v5M6 9v5h5" />
        <path d="M5 5h2v2H5z" fill="currentColor" stroke="none" />
      </>
    ),
    equipment: <path d="M3 3h14v14H3zM8 3v14M12 6h2M11 11h4M11 14h4M5.5 6v2" />,
    handoff: <path d="M10 3H3v14h7M10 10h8M14 6l4 4-4 4M6 6h2M6 10h1M6 14h2" />,
    review: <path d="M13 2.5H4v15h12v-8M7 7l3 3 7-7M7 14h5" />,
    inbox: <path d="M3 3h14v14H3zM3 11h4l1.5 2h3l1.5-2h4M7 6h6M7 8.5h4" />,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="square"
      strokeLinejoin="round"
      aria-hidden="true"
      className={`rivet-icon ${className}`}
      {...props}
    >
      {paths[name]}
    </svg>
  );
}
export function Status({ status }: { status: string }) {
  const names: Record<string, string> = {
    draft: "Draft",
    approved: "Approved",
    in_review: "In review",
    pending: "Needs review",
    applied: "Applied",
    stale: "Out of date",
    rejected: "Dismissed",
    ready: "Ready",
    mapped: "Mapped",
    needs_mapping: "Map columns",
    queued: "Queued",
    failed: "Failed",
    unreviewed: "To review",
    executing: "Working",
    waiting_for_input: "Needs input",
    ready_for_review: "Ready for review",
    cancelled: "Cancelled",
    completed: "Completed",
  };
  const StatusIcon = [
    "approved",
    "ready",
    "mapped",
    "applied",
    "completed",
  ].includes(status)
    ? CheckCircle
    : ["failed", "rejected", "cancelled"].includes(status)
      ? XCircle
      : ["pending", "needs_mapping", "waiting_for_input", "stale"].includes(
            status,
          )
        ? WarningCircle
        : ["in_review", "ready_for_review"].includes(status)
          ? CircleHalf
          : ["queued", "executing"].includes(status)
            ? Clock
            : Circle;
  return (
    <span className={"status " + status}>
      <StatusIcon size={12} aria-hidden="true" />
      {names[status] ?? status.replaceAll("_", " ")}
    </span>
  );
}
export function Empty({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      {icon && <div className="empty-icon">{icon}</div>}
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function Modal({
  title,
  eyebrow,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const dialog = ref.current;
    const focus = () => {
      const items = dialog?.querySelectorAll<HTMLElement>(
        'button:not(:disabled),input,select,textarea,a[href],[tabindex="0"]',
      );
      return items ? [...items].filter((x) => x.offsetParent !== null) : [];
    };
    const initial = dialog?.querySelector<HTMLElement>(
      'input:not([readonly]):not([type="checkbox"]):not([type="file"]),textarea,select',
    );
    (initial ?? focus()[0])?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
      }
      if (e.key === "Tab") {
        const items = focus(),
          first = items[0],
          last = items.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        className={"modal " + (wide ? "wide" : "")}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <div>
            {eyebrow && <span className="eyebrow">{eyebrow}</span>}
            <h2>{title}</h2>
          </div>
          <button
            className="icon-button"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="error-note" role="alert">
      <WarningCircle size={17} />
      <span>{message}</span>
    </div>
  );
}
export function Busy({ text = "Working…" }: { text?: string }) {
  return (
    <span className="busy">
      <CircleNotch className="spin" />
      {text}
    </span>
  );
}
export function ArrowButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button className="text-button" onClick={onClick}>
      {children}
      <ArrowRight size={15} />
    </button>
  );
}
export function Tick({ children }: { children: ReactNode }) {
  return (
    <span className="tick">
      <Check weight="bold" size={14} />
      {children}
    </span>
  );
}
