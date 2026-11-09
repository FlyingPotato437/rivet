import { useState } from "react";
import { ArrowUpRight } from "@phosphor-icons/react";
import { api } from "./api";
import { editableNativeDraftUrl, safeOutlookUrl } from "./outlook-workflow";

export function OutlookDraftButton({
  messageId,
  url,
  native = false,
  label = "Open reply in Outlook",
}: {
  messageId?: string;
  url?: string | null;
  native?: boolean;
  label?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fallback = native
    ? editableNativeDraftUrl(url || "")
    : safeOutlookUrl(url || "");
  if (!native || !messageId)
    return fallback ? (
      <a
        className="work-outlook-button"
        href={fallback}
        target="_blank"
        rel="noreferrer"
      >
        {native ? label : "Open email thread"} <ArrowUpRight size={15} />
      </a>
    ) : null;
  async function open() {
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    setBusy(true);
    setError("");
    try {
      const result = await api<{
        draft: { outlook_url: string; rest_item_id?: string };
      }>(
        `/autopilot/outlook-draft/open?internet_message_id=${encodeURIComponent(messageId!)}`,
      );
      const target = editableNativeDraftUrl(
        result.draft.outlook_url,
        result.draft.rest_item_id,
      );
      if (!target)
        throw new Error(
          "Microsoft did not return a usable draft link. Open Outlook Drafts to continue.",
        );
      if (popup) popup.location.href = target;
      else setError("Allow pop-ups to open the reply, or open Outlook Drafts.");
    } catch (e) {
      popup?.close();
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="work-outlook-action">
      <button
        className="work-outlook-button"
        disabled={busy}
        onClick={() => void open()}
      >
        {busy ? "Opening reply…" : label}
        <ArrowUpRight size={15} />
      </button>
      {error && (
        <p role="alert">
          {error}{" "}
          {fallback && (
            <a href={fallback} target="_blank" rel="noreferrer">
              Open the thread
            </a>
          )}
        </p>
      )}
    </div>
  );
}
