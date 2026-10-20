import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { EnvelopeSimple } from "@phosphor-icons/react";
import { api } from "./api";
import { useAccount } from "./Auth";
import { editableNativeDraftUrl } from "./outlook-workflow";

type FileDraftState = {
  reply_id: string | null;
  mailboxes: { id: string; mailbox: string }[];
  draft: { status: string; error: string } | null;
};

export function DocumentOutlookDraftButton({
  orderId,
  replyId,
}: {
  orderId: string;
  replyId: string;
}) {
  const account = useAccount(),
    qc = useQueryClient();
  const writer = ["org:admin", "org:member"].includes(account.role);
  const q = useQuery({
    queryKey: ["document-outlook-draft", orderId],
    queryFn: () =>
      api<FileDraftState>(`/orders/${orderId}/document-outlook-draft`),
    enabled: writer,
    refetchInterval: 10000,
  });
  const [mailbox, setMailbox] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  if (!writer) return null;
  const d = q.data;
  async function open() {
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    setBusy(true);
    setError("");
    try {
      const result = await api<{
        draft: { outlook_url: string; rest_item_id?: string };
      }>(`/orders/${orderId}/document-outlook-draft`, {
        reply_id: replyId,
        connection_id: mailbox || undefined,
      });
      const target = editableNativeDraftUrl(
        result.draft.outlook_url,
        result.draft.rest_item_id,
      );
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["document-outlook-draft", orderId] }),
        qc.invalidateQueries({ queryKey: ["project-work"] }),
      ]);
      if (!target)
        throw new Error("Draft saved. Open Outlook Drafts to continue.");
      if (popup) popup.location.href = target;
      else setError("Draft saved. Allow pop-ups or open Outlook Drafts.");
    } catch (e) {
      popup?.close();
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const existing =
    !!d?.draft && ["created", "existing_draft"].includes(d.draft.status);
  return (
    <div className="document-draft-action">
      {d && !d.mailboxes.length ? (
        <a href="#settings">Connect Outlook to save a draft</a>
      ) : (
        <>
          {d && d.mailboxes.length > 1 && !existing && (
            <select
              aria-label="Outlook draft mailbox"
              value={mailbox}
              onChange={(e) => setMailbox(e.target.value)}
            >
              <option value="">Choose mailbox</option>
              {d.mailboxes.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.mailbox}
                </option>
              ))}
            </select>
          )}
          <button
            className="primary"
            disabled={
              busy ||
              !d?.reply_id ||
              (!existing && d.mailboxes.length > 1 && !mailbox)
            }
            onClick={() => void open()}
          >
            <EnvelopeSimple size={16} />
            {busy
              ? "Opening Outlook…"
              : existing
                ? "Open Outlook draft"
                : "Save to Outlook Drafts"}
          </button>
          {!existing && d?.mailboxes.length ? (
            <small>
              New draft · choose the recipient and attachments in Outlook
            </small>
          ) : null}
        </>
      )}
      {(error || q.isError || d?.draft?.error) && (
        <p role="status">
          {error ||
            d?.draft?.error ||
            "Outlook status is unavailable. Try again shortly."}
        </p>
      )}
    </div>
  );
}
