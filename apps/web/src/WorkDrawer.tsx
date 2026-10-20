import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "@phosphor-icons/react";
import "./today-story.css";

/** One layer of detail, with keyboard focus kept in the reading surface. */
export function WorkDrawer({
  title,
  eyebrow,
  children,
  close,
}: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
  close: () => void;
}) {
  const panel = useRef<HTMLElement>(null);
  const onClose = useRef(close);
  onClose.current = close;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    const background = document.getElementById("root");
    const wasInert = background?.inert || false;
    if (background) background.inert = true;
    document.body.style.overflow = "hidden";
    panel.current?.focus();
    function keyboard(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose.current();
      }
      if (e.key !== "Tab") return;
      const all = [
        ...(panel.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
        ) || []),
      ].filter((el) => el.getClientRects().length);
      const first = all[0],
        last = all.at(-1);
      if (!first) {
        e.preventDefault();
        return;
      }
      if (!panel.current?.contains(document.activeElement)) {
        e.preventDefault();
        (e.shiftKey ? last : first)?.focus();
        return;
      }
      if (
        e.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === panel.current)
      ) {
        e.preventDefault();
        last?.focus();
      } else if (
        !e.shiftKey &&
        (document.activeElement === last ||
          document.activeElement === panel.current)
      ) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", keyboard);
    return () => {
      document.body.style.overflow = overflow;
      if (background) background.inert = wasInert;
      document.removeEventListener("keydown", keyboard);
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  useEffect(() => {
    if (!panel.current?.contains(document.activeElement))
      panel.current?.focus();
  }, [title]);
  return createPortal(
    <div
      className="work-drawer-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <aside
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="work-drawer"
      >
        <header className="work-drawer-heading">
          <div>
            {eyebrow && <span>{eyebrow}</span>}
            <h2>{title}</h2>
          </div>
          <button
            className="story-icon-button"
            onClick={close}
            aria-label="Close details"
          >
            <X size={20} />
          </button>
        </header>
        <div className="work-drawer-content">{children}</div>
      </aside>
    </div>,
    document.body,
  );
}
