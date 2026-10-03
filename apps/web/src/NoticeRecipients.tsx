import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowsClockwise } from "@phosphor-icons/react";
import { useAccount } from "./Auth";
import { api } from "./api";
import type { RecordView } from "./record-types";

export function NoticeRecipients({
  w,
  notice,
  busy,
  save,
}: {
  w: RecordView;
  notice: RecordView["notices"][number];
  busy: boolean;
  save: (path: string, body: unknown) => Promise<boolean>;
}) {
  const account = useAccount();
  const [updating, setUpdating] = useState(false);
  const deliveries = useQuery({
    queryKey: ["notice-deliveries", w.order.id],
    queryFn: () =>
      api<{ notice_id: string; status: string; error: string }[]>(
        `/orders/${w.order.id}/notice-deliveries`,
      ),
    refetchInterval: 5000,
  });
  // Older drafts can lack subscriber IDs. Refreshing explicitly attaches the
  // current recipients; order-level recipient changes never rewrite a draft.
  const snapshotIds = notice.recipients.map((recipient) =>
    "id" in recipient ? String(recipient.id) : `email:${recipient.email}`,
  );
  const currentIds = w.subscribers.map((recipient) => recipient.id);
  const differs =
    JSON.stringify([...snapshotIds].sort()) !==
    JSON.stringify([...currentIds].sort());
  if (!differs) return null;
  const delivery = deliveries.data?.find(
    (item) => item.notice_id === notice.id,
  );
  if (delivery || notice.status !== "draft")
    return (
      <p className="record-note">
        This notice keeps its original recipients. Recipients cannot change
        after sending has been queued, including failed sends.
      </p>
    );

  const unavailable =
    !deliveries.data ||
    deliveries.isError ||
    !currentIds.length ||
    currentIds.length > 50;
  return (
    <div className="record-notice-recipient-update">
      <p className="record-note">This draft uses an earlier recipient list.</p>
      {w.subscribers.length ? (
        <details>
          <summary>Current order recipients · {w.subscribers.length}</summary>
          <ul>
            {w.subscribers.map((recipient) => (
              <li key={recipient.id}>
                {recipient.name} · {recipient.email} · {recipient.team}
              </li>
            ))}
          </ul>
        </details>
      ) : (
        <p className="record-note">
          Add a notice recipient before updating this draft.
        </p>
      )}
      {currentIds.length > 50 && (
        <p className="record-note">A notice supports up to 50 recipients.</p>
      )}
      {deliveries.isError && (
        <p className="record-note" role="alert">
          Could not confirm this notice’s sending status.{" "}
          <button
            className="text-button"
            onClick={() => void deliveries.refetch()}
          >
            Retry
          </button>
        </p>
      )}
      <button
        className="text-button"
        disabled={busy || updating || unavailable}
        onClick={async () => {
          if (busy || updating || unavailable) return;
          setUpdating(true);
          try {
            await save(`/notices/${notice.id}/recipients`, {
              expected_version: w.version,
              actor: account.name || "Workspace user",
              reason:
                "Reviewed and replaced draft recipients with the current order recipient list",
              recipient_ids: currentIds,
            });
          } finally {
            setUpdating(false);
          }
        }}
      >
        <ArrowsClockwise size={14} />
        {updating ? "Updating recipients…" : "Use current recipients"}
      </button>
      <p className="record-note">Updates this draft only. No email is sent.</p>
    </div>
  );
}
