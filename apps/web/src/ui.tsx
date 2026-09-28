import { useEffect, useRef, type ReactNode } from "react";
import {
  X,
  ArrowRight,
  Check,
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
  return (
    <span className={"status " + status}>
      <i />
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
