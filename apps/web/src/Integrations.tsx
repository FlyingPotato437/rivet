import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  EnvelopeSimple,
  Copy,
  ArrowClockwise,
  PaperPlaneTilt,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { useAccount } from "./Auth";
import { ErrorNote, Modal } from "./ui";
import type { RecordView } from "./record-types";
import "./integrations.css";

type EmailStatus = {
  configured: boolean;
  sending_ready: boolean;
  receiving_domain: string;
  sender: string;
  webhook_configured: boolean;
  local_only: boolean;
  receiving_access?: boolean;
  message?: string;
};
type Inbox = EmailStatus & {
  address: string;
  enabled: boolean;
  emails: { id: string; status: string; error: string; created_at: string }[];
};

export function IntegrationSettings() {
  const account = useAccount();
  const status = useQuery({
    queryKey: ["email-integration"],
    queryFn: () => api<EmailStatus>("/integrations/email"),
  });
  const [checked, setChecked] = useState<EmailStatus>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const data = checked || status.data;
  return (
    <section className="settings-section">
      <h3>Email · Resend</h3>
      <div>
        <span>
          Connection
          <small>
            {data?.configured
              ? "A server-side API key is saved."
              : "No Resend API key configured."}
          </small>
        </span>
        <span className="micro-tag">
          {data?.configured ? "Configured" : "Setup needed"}
        </span>
      </div>
      <div>
        <span>
          Sending
          <small>
            {data?.sender || "Add a verified sender address to enable sending."}
          </small>
        </span>
        <span className="micro-tag">
          {data?.sending_ready ? "Ready" : "Sender needed"}
        </span>
      </div>
      <div>
        <span>
          Forwarding
          <small>
            {data?.receiving_domain ||
              "Add your Resend receiving domain and a Full access API key."}
          </small>
        </span>
        <span className="micro-tag">
          {data?.receiving_domain ? "Domain set" : "Setup needed"}
        </span>
      </div>
      <div>
        <span>
          Automatic delivery
          <small>
            {data?.local_only
              ? "Running locally. Use Check inbox until a public endpoint is deployed."
              : data?.webhook_configured
                ? "Signed incoming email events are enabled."
                : "Configure the receiving webhook after deployment."}
          </small>
        </span>
        <span className="micro-tag">
          {data?.webhook_configured && !data.local_only
            ? "Enabled"
            : "Pending deployment"}
        </span>
      </div>
      {account.role === "org:admin" && (
        <button
          className="secondary"
          disabled={busy || !data?.configured}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              setChecked(
                await api<EmailStatus>("/integrations/email/check", {}),
              );
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <ArrowClockwise size={16} />
          {busy ? "Checking…" : "Check receiving access"}
        </button>
      )}
      {checked?.message && (
        <p
          className={
            checked.receiving_access ? "record-note" : "auth-inline-error"
          }
          role="status"
        >
          {checked.message}
        </p>
      )}
      {(error || status.isError) && (
        <ErrorNote message={error || (status.error as Error).message} />
      )}
    </section>
  );
}

export function OrderInboxPanel({ id }: { id: string }) {
  const account = useAccount();
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["order-inbox", id],
    queryFn: () => api<Inbox>(`/orders/${id}/inbox`),
    refetchInterval: 10000,
  });
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState("");
  const data = query.data;
  const act = async (suffix: string) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api<{ message?: string }>(
        `/orders/${id}/inbox${suffix}`,
        {},
      );
      setMessage(result.message || "Forwarding address created.");
      await query.refetch();
      void qc.invalidateQueries({ queryKey: ["record", id] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="email-inbox">
      <div className="email-inbox-heading">
        <EnvelopeSimple size={22} />
        <div>
          <h3>Forward email to this order</h3>
          <p>
            Keep the original email and its attachments with the source
            documents. Imported comments still require review.
          </p>
        </div>
      </div>
      {data?.address ? (
        <>
          <div className="email-address">
            <code>{data.address}</code>
            <button
              className="icon-button"
              aria-label="Copy forwarding address"
              onClick={() =>
                void navigator.clipboard
                  .writeText(data.address)
                  .then(() => setMessage("Address copied."))
                  .catch(() =>
                    setError("Select and copy the address manually."),
                  )
              }
            >
              <Copy size={16} />
            </button>
          </div>
          {account.role === "org:admin" && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void act("/check")}
            >
              <ArrowClockwise size={16} />
              {busy ? "Checking…" : "Check inbox"}
            </button>
          )}
          <p className="record-note">
            {data.local_only
              ? "Check inbox imports matching emails from the latest 100 messages while Rivet runs locally."
              : "Incoming emails are processed through the receiving webhook."}
          </p>
        </>
      ) : (
        <>
          <p className="record-note">
            {data?.receiving_domain
              ? "Create a unique forwarding address for this order."
              : "Forwarding needs a receiving domain and a Resend key with receiving access. You can import saved .eml files now."}
          </p>
          {account.role === "org:admin" && data?.receiving_domain && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void act("")}
            >
              Create forwarding address
            </button>
          )}
        </>
      )}
      {message && (
        <p className="record-note" role="status">
          {message}
        </p>
      )}
      {(error || query.isError) && (
        <ErrorNote message={error || (query.error as Error).message} />
      )}
      {!!data?.emails.length && (
        <div className="email-intake-history">
          {data.emails.map((e) => (
            <div key={e.id}>
              <span>{when(e.created_at)}</span>
              <strong>
                {e.status === "imported" ? "Imported for review" : e.status}
              </strong>
              {e.error && <small>{e.error}</small>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function SendNoticeButton({
  w,
  notice,
}: {
  w: RecordView;
  notice: RecordView["notices"][number];
}) {
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const status = useQuery({
    queryKey: ["email-integration"],
    queryFn: () => api<EmailStatus>("/integrations/email"),
  });
  const deliveries = useQuery({
    queryKey: ["notice-deliveries", w.order.id],
    queryFn: () =>
      api<{ notice_id: string; status: string; error: string }[]>(
        `/orders/${w.order.id}/notice-deliveries`,
      ),
    refetchInterval: 5000,
  });
  const sent = deliveries.data?.find((d) => d.notice_id === notice.id);
  if (sent)
    return (
      <p className="record-note" role="status">
        {sent.status === "sent"
          ? "Sent via Resend"
          : sent.status === "failed"
            ? `Sending failed: ${sent.error}`
            : "Sending via Resend…"}
      </p>
    );
  return (
    <>
      <button
        className="text-button"
        disabled={!status.data?.sending_ready}
        onClick={() => setOpen(true)}
      >
        <PaperPlaneTilt size={15} />
        {status.data?.sending_ready
          ? "Review & send"
          : "Configure sender to send from Rivet"}
      </button>
      {open && (
        <Modal title="Send change notice" onClose={() => setOpen(false)}>
          <p>This sends the message below to these recipients.</p>
          <ul>
            {notice.recipients.map((r) => (
              <li key={r.email}>
                {r.name} · {r.email}
              </li>
            ))}
          </ul>
          <div className="email-notice-preview">
            <strong>
              {w.order.number} — {notice.title}
            </strong>
            <p>{notice.body}</p>
          </div>
          <p className="record-note">
            No attachments or source documents will be included.
          </p>
          {error && <ErrorNote message={error} />}
          <footer className="modal-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              Cancel
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  await api(
                    `/orders/${w.order.id}/record/notices/${notice.id}/send`,
                    { expected_version: w.version },
                  );
                  await deliveries.refetch();
                  setOpen(false);
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "Queuing…" : "Send notice"}
            </button>
          </footer>
        </Modal>
      )}
    </>
  );
}
