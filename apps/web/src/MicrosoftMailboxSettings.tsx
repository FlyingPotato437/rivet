import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowClockwise,
  ArrowUpRight,
  EnvelopeSimple,
  Pause,
  Play,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { useAccount } from "./Auth";

type MailboxFolder = {
  id: string;
  folder_id: string;
  state: string;
  last_attempt_at: string | null;
  last_synced_at: string | null;
  error: string;
};
type Connection = {
  id: string;
  mailbox: string;
  microsoft_tenant_id: string;
  update_recipient: string;
  enabled: boolean;
  status: string;
  sync_current: boolean;
  coverage: string;
  poll_interval_seconds: number;
  folders: MailboxFolder[];
  counts: Record<string, number>;
};
type Receipt = {
  id: string;
  intake_id?: string | null;
  subject?: string;
  sender?: string;
  status: string;
  error: string;
  order_id: string | null;
  attempts: number;
  received_at: string | null;
  next_attempt_at: string | null;
};
const statusLabels: Record<string, string> = {
  current: "Up to date",
  paused: "Paused",
  attention_required: "Check required",
  pending: "Queued",
  retry: "Retry scheduled",
  imported: "Captured",
  needs_confirmation: "Confirm order",
  not_order_related: "No order context",
  set_aside: "Set aside",
  needs_attention: "Needs attention",
  removed_before_capture: "Removed before capture",
  initial_sync: "Initial sync",
  excluded_draft: "Draft skipped",
};

export function MicrosoftMailboxSettings() {
  const admin = useAccount().role === "org:admin";
  const query = useQuery({
    queryKey: ["microsoft-mailboxes"],
    queryFn: () =>
      api<{ provider_configured: boolean; items: Connection[] }>("/mailboxes"),
    refetchInterval: 15000,
  });
  const [showForm, setShowForm] = useState(false);
  const [tenant, setTenant] = useState("");
  const [mailbox, setMailbox] = useState("");
  const [folders, setFolders] = useState("inbox");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function register(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !confirmed) return;
    const folderIds = [
      ...new Set(
        folders
          .split(/[\n,]/)
          .map((x) => x.trim())
          .filter(Boolean),
      ),
    ];
    if (
      !folderIds.length ||
      folderIds.length > 20 ||
      folderIds.some((id) => ["*", "all"].includes(id.toLowerCase()))
    ) {
      setError(
        "List one to twenty specific folders. Whole-mailbox wildcards are not supported.",
      );
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api<Connection>("/mailboxes", {
        microsoft_tenant_id: tenant.trim(),
        mailbox: mailbox.trim(),
        folder_ids: folderIds,
      });
      await query.refetch();
      setShowForm(false);
      setConfirmed(false);
      setNotice(
        "Mailbox registered. Wait for a successful folder check before treating synchronization as active.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="ms-mailbox-settings"
      aria-labelledby="microsoft-mailbox-title"
    >
      <header>
        <div>
          <div className="ms-mailbox-kicker">
            <EnvelopeSimple size={18} />
            Microsoft 365
          </div>
          <h3 id="microsoft-mailbox-title">Mailbox connection</h3>
          <p>
            Rivet captures mail from the folders you authorize. Each discovered
            message gets a tracked receipt.
          </p>
        </div>
        <a
          href="/outlook/taskpane.html"
          target="_blank"
          rel="noopener noreferrer"
        >
          Open reply assistant <ArrowUpRight size={15} />
        </a>
      </header>
      {query.isPending && <p role="status">Checking mailbox setup…</p>}
      {query.isError && (
        <p className="ms-mailbox-error" role="alert">
          {(query.error as Error).message}
        </p>
      )}
      {query.data && (
        <>
          {!query.data.items.length && (
            <div className="ms-mailbox-empty">
              <strong>No mailbox connected</strong>
              <p>
                {query.data.provider_configured
                  ? "The Microsoft provider is configured. An administrator can register an authorized mailbox and its folders below."
                  : "A Microsoft 365 tenant, authorized test mailbox, and server-side Graph credentials are still needed. No inbox is being monitored."}
              </p>
            </div>
          )}
          {query.data.items.map((connection) => (
            <MailboxConnection
              key={connection.id}
              connection={connection}
              admin={admin}
              refresh={() => query.refetch()}
            />
          ))}
          {admin && (
            <div className="ms-mailbox-register">
              {!showForm ? (
                <button
                  className="secondary"
                  type="button"
                  onClick={() => setShowForm(true)}
                >
                  {query.data.items.length
                    ? "Register another mailbox or folder"
                    : "Set up an authorized mailbox"}
                </button>
              ) : (
                <form onSubmit={register}>
                  <h4>Register a Microsoft 365 mailbox</h4>
                  <p>
                    This records an already authorized mailbox. It does not
                    grant Microsoft access or purchase an account.
                  </p>
                  <div className="ms-mailbox-fields">
                    <label>
                      Microsoft tenant ID
                      <input
                        value={tenant}
                        onChange={(e) => setTenant(e.target.value)}
                        required
                        pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
                        placeholder="Tenant GUID from your administrator"
                        disabled={busy}
                      />
                    </label>
                    <label>
                      Mailbox address
                      <input
                        type="email"
                        value={mailbox}
                        onChange={(e) => setMailbox(e.target.value)}
                        required
                        placeholder="orders@your-company.com"
                        disabled={busy}
                      />
                    </label>
                  </div>
                  <label>
                    Folders to monitor
                    <textarea
                      rows={3}
                      value={folders}
                      onChange={(e) => setFolders(e.target.value)}
                      required
                      disabled={busy}
                      aria-describedby="mailbox-folder-help"
                    />
                  </label>
                  <p id="mailbox-folder-help" className="ms-mailbox-help">
                    One Graph folder ID per line. Use “inbox” for the Inbox.
                    Mail rules may move messages to other folders; list those
                    explicitly. Registering an existing mailbox adds folders and
                    does not remove its current scope.
                  </p>
                  <label className="ms-mailbox-confirm">
                    <input
                      type="checkbox"
                      checked={confirmed}
                      onChange={(e) => setConfirmed(e.target.checked)}
                      disabled={busy}
                    />
                    <span>
                      I have authorized this mailbox and reviewed the folders
                      Rivet will monitor.
                    </span>
                  </label>
                  {!query.data.provider_configured && (
                    <p className="ms-mailbox-help">
                      Registration is unavailable until the Microsoft provider
                      and this mailbox's allowlist are configured on the server.
                      Keep credentials out of this form.
                    </p>
                  )}
                  <div className="ms-mailbox-actions">
                    <button
                      className="primary"
                      type="submit"
                      disabled={
                        busy || !confirmed || !query.data.provider_configured
                      }
                    >
                      {busy ? "Registering…" : "Register mailbox"}
                    </button>
                    <button
                      className="text-button"
                      type="button"
                      onClick={() => setShowForm(false)}
                      disabled={busy}
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              )}
            </div>
          )}
        </>
      )}
      {error && (
        <p className="ms-mailbox-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="ms-mailbox-notice" role="status">
          {notice}
        </p>
      )}
      <p className="ms-mailbox-scope">
        Coverage applies only to configured folders. Mail removed before capture
        or moved outside that scope can require recovery; a connection alone is
        not a guarantee that every email was captured.
      </p>
    </section>
  );
}

function MailboxConnection({
  connection,
  admin,
  refresh,
}: {
  connection: Connection;
  admin: boolean;
  refresh: () => Promise<unknown>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const receipts = useQuery({
    queryKey: ["microsoft-receipts", connection.id],
    queryFn: () =>
      api<{ items: Receipt[] }>(`/mailboxes/${connection.id}/receipts`),
    enabled: expanded,
    refetchInterval: expanded ? 15000 : false,
  });
  async function act(action: string, body?: unknown, method?: string) {
    if (busy) return;
    setBusy(action || "state");
    setError("");
    setNotice("");
    try {
      await api(`/mailboxes/${connection.id}${action}`, body || {}, method);
      await refresh();
      if (expanded) await receipts.refetch();
      setNotice(
        action.includes("retry")
          ? "Retry queued. The receipt will update after the worker processes it."
          : action === "/reconcile"
            ? "Mailbox check scheduled. Existing checkpoints are preserved."
            : connection.enabled
              ? "Monitoring paused. Existing records are retained."
              : "Monitoring resumed. A folder check is scheduled.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  const needsAttention =
    (connection.counts.needs_attention || 0) +
    (connection.counts.removed_before_capture || 0);
  return (
    <article className="ms-mailbox-connection">
      <div className="ms-mailbox-connection-title">
        <h4>{connection.mailbox}</h4>
        <span
          className={
            connection.status === "current"
              ? "ms-mailbox-state current"
              : "ms-mailbox-state"
          }
        >
          {statusLabels[connection.status] || connection.status}
        </span>
      </div>
      <div className="ms-mailbox-counts">
        <span>
          <strong>{connection.counts.imported || 0}</strong> captured
        </span>
        <span>
          <strong>{connection.counts.pending || 0}</strong> queued
        </span>
        <span>
          <strong>{connection.counts.retry || 0}</strong> retrying
        </span>
        <span>
          <strong>{needsAttention}</strong> need attention
        </span>
        {!!connection.counts.needs_confirmation && (
          <span>
            <strong>{connection.counts.needs_confirmation}</strong> need an
            order
          </span>
        )}
      </div>
      <div className="ms-mailbox-folders">
        {connection.folders.map((folder) => (
          <div key={folder.id}>
            <div>
              <strong>{folder.folder_id}</strong>
              <span>{statusLabels[folder.state] || folder.state}</span>
            </div>
            <p>
              {folder.last_synced_at
                ? `Last complete check: ${when(folder.last_synced_at)}`
                : folder.last_attempt_at
                  ? `Attempted ${when(folder.last_attempt_at)}; no complete check yet`
                  : "Waiting for the first folder check"}
            </p>
            {folder.error && <p className="ms-mailbox-error">{folder.error}</p>}
          </div>
        ))}
      </div>
      <p className="ms-mailbox-help">
        Scheduled checks: every{" "}
        {Math.round(connection.poll_interval_seconds / 60)} minutes.
        {connection.update_recipient &&
          ` Update recipient: ${connection.update_recipient}.`}
      </p>
      {admin && (
        <div className="ms-mailbox-actions">
          <button
            type="button"
            className="secondary"
            disabled={!!busy || !connection.enabled}
            onClick={() => void act("/reconcile")}
          >
            <ArrowClockwise size={16} />
            {busy === "/reconcile" ? "Scheduling…" : "Check mailbox now"}
          </button>
          <button
            type="button"
            className="text-button"
            disabled={!!busy}
            onClick={() =>
              void act("", { enabled: !connection.enabled }, "PATCH")
            }
          >
            {connection.enabled ? <Pause size={15} /> : <Play size={15} />}
            {connection.enabled ? "Pause" : "Resume"}
          </button>
        </div>
      )}
      {error && (
        <p className="ms-mailbox-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="ms-mailbox-notice" role="status">
          {notice}
        </p>
      )}
      <details
        className="ms-mailbox-receipts"
        onToggle={(event) => setExpanded(event.currentTarget.open)}
      >
        <summary>Message receipts</summary>
        {receipts.isPending && expanded && (
          <p role="status">Loading receipts…</p>
        )}
        {receipts.isError && (
          <p className="ms-mailbox-error" role="alert">
            {(receipts.error as Error).message}
          </p>
        )}
        {receipts.data && !receipts.data.items.length && (
          <p>
            No messages have been recorded yet. Wait for the first folder check.
          </p>
        )}
        {receipts.data?.items.map((receipt) => (
          <div className="ms-mailbox-receipt" key={receipt.id}>
            <div>
              <strong>{receipt.subject || "Message without a subject"}</strong>
              <span>{statusLabels[receipt.status] || receipt.status}</span>
            </div>
            <p>
              {receipt.sender || "Sender unavailable"}
              {receipt.received_at ? ` · ${when(receipt.received_at)}` : ""}
            </p>
            {receipt.error && (
              <p className="ms-mailbox-error">{receipt.error}</p>
            )}
            <div className="ms-mailbox-receipt-actions">
              {receipt.order_id && (
                <a href={`#order/${receipt.order_id}`}>
                  View order <ArrowUpRight size={14} />
                </a>
              )}
              {receipt.intake_id &&
                [
                  "needs_confirmation",
                  "not_order_related",
                  "set_aside",
                ].includes(receipt.status) && (
                  <a
                    href={`#inbox?intake=${encodeURIComponent(receipt.intake_id)}`}
                  >
                    {receipt.status === "needs_confirmation"
                      ? "Review order match"
                      : "View email and recover"}{" "}
                    <ArrowUpRight size={14} />
                  </a>
                )}
              {admin &&
                ["retry", "needs_attention", "removed_before_capture"].includes(
                  receipt.status,
                ) && (
                  <button
                    className="text-button"
                    disabled={!!busy}
                    onClick={() => void act(`/receipts/${receipt.id}/retry`)}
                  >
                    Retry capture
                  </button>
                )}
            </div>
          </div>
        ))}
      </details>
    </article>
  );
}
