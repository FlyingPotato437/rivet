import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  EnvelopeSimple,
  Copy,
  ArrowClockwise,
  PaperPlaneTilt,
  DownloadSimple,
  ArrowSquareOut,
} from "@phosphor-icons/react";
import { api, apiFetch, when } from "./api";
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

type AdoptionStatus = {
  forwarding: {
    status: string;
    address_count: number;
    automatic_delivery_configured: boolean;
    team_address: string;
    team_address_enabled: boolean;
  };
  integrations: { id: string; name: string; status: string; detail: string }[];
};

export function AdoptionReadiness({ compact = false }: { compact?: boolean }) {
  const state = useQuery({
    queryKey: ["adoption-readiness"],
    queryFn: () => api<AdoptionStatus>("/integrations/adoption"),
  });
  const labels: Record<string, string> = {
    available: "Available",
    export_available: "Export available",
    not_connected: "Not connected",
  };
  return (
    <section className={`adoption-readiness${compact ? " is-compact" : ""}`}>
      <header>
        <span className="eyebrow">Start with one returned drawing</span>
        <h3>Your existing tools, with a clear connection status</h3>
        <p>
          Upload a PDF or saved email now. Mailbox and business-system
          connections are optional.
        </p>
      </header>
      {state.isPending && (
        <p className="record-note" role="status">
          Checking workspace setup…
        </p>
      )}
      {state.isError && <ErrorNote message={(state.error as Error).message} />}
      {state.data && (
        <>
          <div className="adoption-integration-list">
            {state.data.integrations.map((integration) => (
              <article key={integration.id}>
                <div>
                  <strong>{integration.name}</strong>
                  <p>{integration.detail}</p>
                </div>
                <span
                  className={`adoption-state ${integration.status === "not_connected" ? "is-later" : "is-available"}`}
                >
                  {labels[integration.status] || integration.status}
                </span>
              </article>
            ))}
          </div>
          <p className="adoption-forwarding-note">
            {state.data.forwarding.team_address_enabled
              ? "A team forwarding address is available in the Inbox. Rivet matches the job and revision, then asks you to confirm uncertain matches."
              : state.data.forwarding.address_count > 0
                ? `${state.data.forwarding.address_count} order forwarding ${state.data.forwarding.address_count === 1 ? "address" : "addresses"} created. Open an order’s documents to copy its address.`
                : "No forwarding address has been created for this team. An administrator can enable forwarding in the Inbox."}{" "}
            {state.data.forwarding.automatic_delivery_configured
              ? "A receiving webhook is configured; verify delivery with a test email during setup."
              : "Automatic email intake is not enabled. Saved email uploads work without it."}
          </p>
        </>
      )}
    </section>
  );
}

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
    <>
      <AdoptionReadiness />
      <section className="settings-section">
        <h3>Email forwarding setup</h3>
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
              {data?.sender ||
                "Add a verified sender address to enable sending."}
            </small>
          </span>
          <span className="micro-tag">
            {data?.sending_ready ? "Configured" : "Sender needed"}
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
                : data?.webhook_configured &&
                    data.configured &&
                    data.receiving_domain
                  ? "A receiving webhook is configured. Verify delivery with a test email."
                  : "Configure the receiving webhook after deployment."}
            </small>
          </span>
          <span className="micro-tag">
            {data?.webhook_configured &&
            data.configured &&
            data.receiving_domain &&
            !data.local_only
              ? "Configured"
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
    </>
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
      {data?.address && data.enabled ? (
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
              : data.webhook_configured && data.configured
                ? "A receiving webhook is configured. Confirm a test email appears below before relying on automatic intake."
                : "Automatic intake is not configured. An administrator can check the inbox manually."}
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
      <ReviewBrief id={id} />
    </div>
  );
}

type TeamInbox = EmailStatus & {
  address: string;
  enabled: boolean;
  automatic_delivery_configured: boolean;
  emails: {
    id: string;
    status: string;
    order_id: string | null;
    intake_id: string | null;
    error: string;
    created_at: string;
  }[];
};

export function TeamInboxControl({
  navigate,
}: {
  navigate?: (to: string) => void;
}) {
  const account = useAccount();
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["team-inbox"],
    queryFn: () => api<TeamInbox>("/integrations/team-inbox"),
    refetchInterval: 15000,
  });
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const data = query.data;
  const active = Boolean(data?.enabled && data.address);
  const admin = account.role === "org:admin";
  async function act(action: string) {
    setBusy(action || "enable");
    setError("");
    setMessage("");
    try {
      const result = await api<{ message?: string }>(
        `/integrations/team-inbox${action}`,
        {},
      );
      setMessage(
        result.message ||
          (action === "/disable"
            ? "Forwarding paused. Saved emails are retained."
            : action.startsWith("/emails/")
              ? "Email queued for retry."
              : "Team address created. Forward a nonconfidential test email to verify delivery."),
      );
      await Promise.all([
        query.refetch(),
        qc.invalidateQueries({ queryKey: ["adoption-readiness"] }),
        qc.invalidateQueries({ queryKey: ["record-intake"] }),
      ]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <div className="team-inbox-control">
      <div className="team-inbox-line">
        <EnvelopeSimple size={18} />
        {active ? (
          <>
            <span>
              Or forward to <code>{data!.address}</code>
            </span>
            <button
              className="icon-button"
              aria-label="Copy team forwarding address"
              onClick={() => {
                void navigator.clipboard
                  .writeText(data!.address)
                  .then(() => setMessage("Team address copied."))
                  .catch(() =>
                    setError("Select and copy the address manually."),
                  );
              }}
            >
              <Copy size={16} />
            </button>
            <span className="team-inbox-state">
              {data!.automatic_delivery_configured
                ? "Forwarding configured"
                : "Manual inbox check"}
            </span>
            {admin && (
              <button
                className="text-button"
                disabled={!!busy}
                onClick={() => void act("/check")}
              >
                <ArrowClockwise size={15} />
                {busy === "/check" ? "Checking…" : "Check inbox"}
              </button>
            )}
          </>
        ) : (
          <>
            <span>
              {query.isPending
                ? "Checking email forwarding…"
                : "Forward returned drawings to one team address."}
            </span>
            {admin && data?.configured && data.receiving_domain ? (
              <button
                className="text-button"
                disabled={!!busy}
                onClick={() => void act("")}
              >
                {busy ? "Enabling…" : "Enable forwarding"}
              </button>
            ) : (
              navigate &&
              !query.isPending && (
                <button
                  className="text-button"
                  onClick={() => navigate("settings")}
                >
                  Email setup <ArrowSquareOut size={14} />
                </button>
              )
            )}
          </>
        )}
      </div>
      {active && (
        <details className="team-inbox-details">
          <summary>
            Forwarding details
            {data!.emails.length
              ? ` · ${data!.emails.length} recent messages`
              : ""}
          </summary>
          <p>
            {data!.automatic_delivery_configured
              ? "A receiving webhook is configured. Send a nonconfidential test email and confirm it appears here before relying on automatic intake."
              : "Automatic intake is not configured. An administrator can use Check inbox to import matching messages from the latest 100 received emails."}{" "}
            Uncertain job matches stay in this Inbox until you confirm them.
          </p>
          {!!data!.emails.length && (
            <div className="team-inbox-history">
              {data!.emails.map((email) => (
                <div key={email.id}>
                  <span>{when(email.created_at)}</span>
                  <strong>
                    {(
                      {
                        imported: "Imported",
                        needs_confirmation: "Confirm order",
                        queued: "Queued",
                        failed: "Needs attention",
                      } as Record<string, string>
                    )[email.status] || email.status}
                  </strong>
                  {email.order_id && navigate && (
                    <button
                      className="text-button"
                      onClick={() => navigate(`order/${email.order_id}`)}
                    >
                      Open order <ArrowSquareOut size={13} />
                    </button>
                  )}
                  {email.status === "failed" && admin && (
                    <button
                      className="text-button"
                      disabled={!!busy}
                      onClick={() => void act(`/emails/${email.id}/retry`)}
                    >
                      <ArrowClockwise size={14} />
                      {busy === `/emails/${email.id}/retry`
                        ? "Retrying…"
                        : "Retry import"}
                    </button>
                  )}
                  {email.error && <p>{email.error}</p>}
                </div>
              ))}
            </div>
          )}
          {admin && (
            <button
              className="text-button"
              disabled={!!busy}
              onClick={() => void act("/disable")}
            >
              {busy === "/disable" ? "Pausing…" : "Pause forwarding"}
            </button>
          )}
        </details>
      )}
      {message && (
        <p className="team-inbox-feedback" role="status">
          {message}
        </p>
      )}
      {(error || query.isError) && (
        <ErrorNote message={error || (query.error as Error).message} />
      )}
    </div>
  );
}

type Brief = {
  subject: string;
  summary: string;
  text: string;
  version: number;
  order_url: string;
  items: {
    id: string;
    label: string;
    title: string;
    detail: string;
    url: string;
    comment_number: string;
  }[];
};

export function ReviewBrief({
  id,
  compact = false,
}: {
  id: string;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const preview = useQuery({
    queryKey: ["review-brief", id],
    queryFn: () => api<Brief>(`/orders/${id}/record/digest`),
    enabled: open,
    staleTime: 0,
  });
  async function download() {
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(
        `/api/orders/${id}/record/digest.eml?expected_version=${preview.data?.version}`,
      );
      if (response.status === 409) {
        await preview.refetch();
        throw new Error(
          "The order changed. Review the updated brief, then download again.",
        );
      }
      if (!response.ok)
        throw new Error(
          "The brief could not be prepared. Refresh and try again.",
        );
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = "rivet-review-brief.eml";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={`review-brief-entry${compact ? " is-compact" : ""}`}>
      {!compact && (
        <div>
          <span className="eyebrow">Prepared automatically</span>
          <h3>Bring the review back to email</h3>
          <p>
            One brief with comment counts, revision flags and links to the
            source review.
          </p>
        </div>
      )}
      <button className="secondary" onClick={() => setOpen(true)}>
        <EnvelopeSimple size={17} /> Preview email brief
      </button>
      {open && (
        <Modal title="Review brief" onClose={() => setOpen(false)}>
          {preview.isPending && (
            <p role="status">Preparing the current review brief…</p>
          )}
          {(error || preview.isError) && (
            <ErrorNote message={error || (preview.error as Error).message} />
          )}
          {preview.data && (
            <>
              <div className="review-brief-preview">
                <span className="adoption-state is-available">
                  Automatically prepared · Draft
                </span>
                <h3>{preview.data.subject}</h3>
                <p>{preview.data.summary}</p>
                {preview.data.items.map((item) => (
                  <article key={item.id}>
                    <span>
                      {item.label} · Comment {item.comment_number}
                    </span>
                    <strong>{item.title}</strong>
                    <p>{item.detail}</p>
                    {item.url && (
                      <a href={item.url} onClick={() => setOpen(false)}>
                        Review comment <ArrowSquareOut size={14} />
                      </a>
                    )}
                  </article>
                ))}
                {!preview.data.items.length && (
                  <p>
                    No repeat, prior-open, approval or scope flags are recorded.
                    This does not verify the drawing’s engineering accuracy.
                  </p>
                )}
                <details>
                  <summary>Full email text</summary>
                  <pre>{preview.data.text}</pre>
                </details>
              </div>
              <p className="record-note">
                Review the draft and choose recipients in your email app. Links
                require access to this team. Downloading does not send a message
                or approve the record.
              </p>
              <footer className="modal-actions">
                <button className="secondary" onClick={() => setOpen(false)}>
                  Close
                </button>
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => void download()}
                >
                  <DownloadSimple size={16} />
                  {busy ? "Preparing…" : "Download email draft"}
                </button>
              </footer>
            </>
          )}
        </Modal>
      )}
    </section>
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
