import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  MagnifyingGlass,
  Plus,
  ArrowRight,
  CircleNotch,
  EnvelopeSimple,
  CheckCircle,
  WarningCircle,
} from "@phosphor-icons/react";
import { useAccount } from "./Auth";
import { api, when } from "./api";
import { useRecordInbox, useRecordOrders } from "./RecordFeed";
import { IntakeConfirmation, RecordIntake, type Intake } from "./RecordIntake";
import {
  buildWorkStream,
  type WorkActivity,
  type StreamItem,
} from "./work-stream";
import { OutlookDraftButton } from "./OutlookDraftButton";
import {
  lastMailboxCheck,
  mailboxCheckProgress,
  mailboxCheckTargets,
  requestMailboxChecks,
  type MailboxCheckTarget,
  type WorkMailbox,
} from "./mailbox-check";
import { ErrorNote, RivetIcon } from "./ui";
import { CompanyLearning } from "./CompanyLearning";
import "./straightforward-work.css";

type Mailboxes = { items: WorkMailbox[] };
export function YourWork({ navigate }: { navigate: (to: string) => void }) {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [history, setHistory] = useState(false);
  const [adding, setAdding] = useState(false);
  const focus = new URLSearchParams(location.hash.split("?")[1] || "").get(
    "intake",
  );
  const orders = useRecordOrders();
  const records = useRecordInbox();
  const activity = useQuery({
    queryKey: ["autopilot"],
    queryFn: () => api<WorkActivity>("/autopilot/activity"),
    refetchInterval: (query) =>
      query.state.data?.feed.some((event) =>
        ["queued", "running"].includes(event.preparation?.status || ""),
      )
        ? 2500
        : 8000,
  });
  const incoming = useQuery({
    queryKey: ["record-intake", "pending"],
    queryFn: () =>
      api<{ items: Intake[] }>("/records/intake?status=needs_confirmation"),
    refetchInterval: 15000,
  });
  const mailbox = useQuery({
    queryKey: ["work-mailboxes"],
    queryFn: () => api<Mailboxes>("/mailboxes"),
    refetchInterval: 30000,
  });
  const stream = useMemo(
    () =>
      buildWorkStream(
        activity.data,
        records.data?.items || [],
        orders.data?.orders || [],
      ),
    [activity.data, records.data, orders.data],
  );
  const matching =
    incoming.data?.items.filter(
      (item) => item.status === "needs_confirmation",
    ) || [];
  const text = search.trim().toLowerCase();
  // Keep the result of today's correspondence visible after it leaves Drafts.
  const recentSent = new Set(
    stream
      .filter(
        (row) =>
          row.state === "done" &&
          !!row.outcome &&
          Date.parse(row.at) > Date.now() - 86400000,
      )
      .map((row) => row.id),
  );
  const visible = stream.filter(
    (row) =>
      (row.state !== "done" || recentSent.has(row.id)) &&
      `${row.subject} ${row.sender} ${row.orderNumber} ${row.detail}`
        .toLowerCase()
        .includes(text),
  );
  const completed = stream.filter(
    (row) =>
      row.state === "done" &&
      !recentSent.has(row.id) &&
      `${row.subject} ${row.sender} ${row.orderNumber}`
        .toLowerCase()
        .includes(text),
  );
  const questions = matching.filter((item) =>
    `${item.email?.subject || item.filename} ${item.reason}`
      .toLowerCase()
      .includes(text),
  );
  const connected = mailbox.data?.items.some((item) => item.enabled);
  const mailboxAttention = mailbox.data?.items.some(
    (item) => item.enabled && item.status !== "current",
  );
  const loading = activity.isPending || records.isPending || incoming.isPending;
  const incomplete = activity.isError || records.isError || incoming.isError;
  const section = useRef<HTMLElement>(null);
  async function refresh() {
    await Promise.all([
      activity.refetch(),
      records.refetch(),
      incoming.refetch(),
      orders.refetch(),
    ]);
  }
  function confirmed(next: Intake) {
    qc.setQueryData(["record-intake", next.intake_id], next);
    void qc.invalidateQueries({ queryKey: ["record-intake"] });
    void qc.invalidateQueries({ queryKey: ["record-inbox"] });
    void qc.invalidateQueries({ queryKey: ["records"] });
    void qc.invalidateQueries({ queryKey: ["autopilot"] });
  }
  return (
    <main className="page your-work" ref={section}>
      <header className="work-page-heading">
        <div>
          <span className="work-eyebrow">Your workspace</span>
          <h1>Your work</h1>
          <p>
            {connected
              ? "Prepared replies are in Outlook. Their supporting record is here."
              : "Prepared responses and their supporting documents."}
          </p>
        </div>
        <button
          className="work-add-file"
          onClick={() => setAdding(!adding)}
          aria-expanded={adding}
        >
          <Plus size={17} />
          {adding ? "Close upload" : "Add a file"}
        </button>
      </header>
      <div className="work-connection-line">
        <span
          className={`work-connection-dot${connected ? " connected" : ""}`}
        />
        <span>
          {mailbox.isError
            ? "Mailbox status unavailable"
            : connected
              ? mailboxAttention
                ? "Outlook sync needs attention"
                : "Outlook connected"
              : mailbox.isPending
                ? "Checking Outlook…"
                : "Connect Outlook to prepare replies directly in your inbox"}
        </span>
        {connected && <CheckInboxNow mailboxes={mailbox.data?.items || []} />}
        <button
          className="work-connection-settings"
          onClick={() => navigate("settings")}
        >
          {connected ? "Settings" : "Connect Outlook"}
        </button>
      </div>
      {connected && activity.data && (
        <dl
          className="work-summary"
          aria-label="Rivet activity in the last seven days"
        >
          <div>
            <dt>Emails checked</dt>
            <dd>{activity.data.summary.emails_read}</dd>
          </div>
          <div>
            <dt>Filed to orders</dt>
            <dd>{activity.data.summary.filed_to_orders ?? "—"}</dd>
          </div>
          <div>
            <dt>In Outlook drafts</dt>
            <dd>
              {activity.data.summary.waiting_in_outlook ??
                activity.data.waiting_in_outlook.length}
            </dd>
          </div>
          <div>
            <dt>Needs you</dt>
            <dd>{activity.data.summary.needs_you}</dd>
          </div>
        </dl>
      )}
      {(adding || focus) && (
        <section className="work-upload-area">
          <RecordIntake
            navigate={navigate}
            orders={orders.data?.orders || []}
          />
          {focus && (
            <button className="text-button" onClick={() => navigate("work")}>
              Back to your work
            </button>
          )}
        </section>
      )}
      <div className="work-list-heading">
        <h2>Latest work</h2>
        <label className="work-find">
          <MagnifyingGlass size={17} />
          <input
            aria-label="Search your work"
            placeholder="Find an email or order"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
      </div>
      {incomplete && (
        <div className="work-load-error">
          <ErrorNote message="Some work could not refresh. Showing what is available; this may not be the complete list." />
          <button className="text-button" onClick={() => void refresh()}>
            Try again
          </button>
        </div>
      )}
      {loading && !stream.length ? (
        <div className="work-loading" aria-label="Loading your work">
          <div />
          <div />
          <div />
        </div>
      ) : (
        <section
          className="work-stream"
          aria-label="Prepared work and open questions"
        >
          {!focus &&
            questions.map((item) => (
              <IntakeConfirmation
                key={item.intake_id}
                item={item}
                orders={orders.data?.orders || []}
                confirmed={confirmed}
              />
            ))}
          {visible.map((row) => (
            <WorkRow key={row.id} row={row} navigate={navigate} />
          ))}
          {!visible.length && !questions.length && !incomplete && (
            <div className="work-quiet">
              <RivetIcon name="agent" size={32} />
              <h2>
                {text
                  ? "No matching work"
                  : connected
                    ? "You’re up to date"
                    : "Start in your inbox"}
              </h2>
              <p>
                {text
                  ? "Try an email subject or job number."
                  : connected
                    ? "New replies and any questions will appear here as Rivet works."
                    : "Connect your Outlook mailbox once. Rivet will capture order mail, read the documents, and prepare the reply."}
              </p>
              {!connected && !text && (
                <button
                  className="primary"
                  onClick={() => navigate("settings")}
                >
                  Connect Outlook <ArrowRight size={16} />
                </button>
              )}
            </div>
          )}
        </section>
      )}
      <footer className="work-history-control">
        <button
          className="text-button"
          onClick={() => setHistory(!history)}
          aria-expanded={history}
        >
          {history ? "Hide completed history" : "Show completed history"}
          {completed.length > 0 && <span>{completed.length}</span>}
        </button>
        <button
          className="text-button"
          disabled={activity.isFetching || records.isFetching}
          onClick={() => void refresh()}
        >
          Refresh
        </button>
      </footer>
      {history && (
        <section
          className="work-stream work-completed"
          aria-label="Completed correspondence"
        >
          {completed.length ? (
            completed.map((row) => (
              <WorkRow key={row.id} row={row} navigate={navigate} />
            ))
          ) : (
            <p className="work-history-empty">
              Sent replies and completed correspondence will appear here.
            </p>
          )}
        </section>
      )}
      <CompanyLearning navigate={navigate} />
    </main>
  );
}
/** Wakes mailbox reconciliation now instead of waiting for the 2-minute cycle. */
export function CheckInboxNow({
  mailboxes,
  showLastCheck = true,
}: {
  mailboxes: WorkMailbox[];
  showLastCheck?: boolean;
}) {
  const qc = useQueryClient();
  const admin = useAccount().role === "org:admin";
  const [state, setState] = useState<
    "idle" | "requesting" | "checking" | "done" | "error" | "slow"
  >("idle");
  const [targets, setTargets] = useState<MailboxCheckTarget[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const alive = useRef(true);
  const busy = state === "requesting" || state === "checking";
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const query = useQuery({
    queryKey: ["work-mailboxes"],
    queryFn: () => api<Mailboxes>("/mailboxes"),
    refetchInterval: state === "checking" ? 2000 : false,
    enabled: state === "checking",
  });
  useEffect(() => {
    if (state !== "checking") return;
    const progress = mailboxCheckProgress(
      targets,
      query.data?.items || mailboxes,
    );
    if (progress.errors.length) {
      setErrors((previous) => [...new Set([...previous, ...progress.errors])]);
      setState("error");
    } else if (progress.complete) {
      setState(errors.length ? "error" : "done");
      for (const key of [
        "autopilot",
        "record-inbox",
        "record-intake",
        "records",
      ])
        void qc.invalidateQueries({ queryKey: [key] });
    }
  }, [state, targets, mailboxes, query.data, qc, errors.length]);
  useEffect(() => {
    if (state !== "checking") return;
    const timeout = setTimeout(() => setState("slow"), 120000);
    const interval = setInterval(() => {
      for (const key of [
        "autopilot",
        "record-inbox",
        "record-intake",
        "records",
      ])
        void qc.invalidateQueries({ queryKey: [key] });
    }, 3000);
    return () => {
      clearTimeout(timeout);
      clearInterval(interval);
    };
  }, [state, qc]);
  useEffect(() => {
    if (state !== "done") return;
    const timeout = setTimeout(() => setState("idle"), 10000);
    return () => clearTimeout(timeout);
  }, [state]);
  async function check() {
    if (busy) return;
    const next = mailboxCheckTargets(mailboxes);
    if (!next.length) return;
    setErrors([]);
    setState("requesting");
    const result = await requestMailboxChecks(next, api);
    if (!alive.current) return;
    setTargets(result.scheduled);
    setErrors(result.errors);
    setState(result.scheduled.length ? "checking" : "error");
    void qc.invalidateQueries({ queryKey: ["work-mailboxes"] });
  }
  const checkedAt = lastMailboxCheck(mailboxes);
  const status =
    state === "done"
      ? "Inbox checked"
      : state === "slow"
        ? "The check is taking longer than expected. Rivet will continue in the background."
        : state === "error"
          ? errors.join(" ") || "The check could not finish. Try again."
          : busy
            ? "Checking monitored Outlook folders…"
            : checkedAt
              ? `Last checked ${when(checkedAt)}`
              : "Waiting for the first inbox check";
  return (
    <div className="work-inbox-check">
      {(showLastCheck || state !== "idle") && (
        <span className={`work-check-status ${state}`} role="status">
          {status}
        </span>
      )}
      {admin && (
        <button
          className="work-check-button"
          onClick={() => void check()}
          disabled={busy}
        >
          {busy && <CircleNotch size={15} className="work-spinning" />}
          {busy
            ? "Checking inbox…"
            : state === "error" || state === "slow"
              ? "Check again"
              : "Check inbox now"}
        </button>
      )}
    </div>
  );
}

function WorkRow({
  row,
  navigate,
}: {
  row: StreamItem;
  navigate: (to: string) => void;
}) {
  const Icon =
    row.state === "attention" && !row.preparedOutputs && !row.nativeDraft
      ? WarningCircle
      : row.state === "working"
        ? CircleNotch
        : row.state === "done"
          ? CheckCircle
          : EnvelopeSimple;
  const label = row.nativeDraft
    ? row.existingDraft
      ? "Your Outlook draft"
      : "Ready in Outlook"
    : row.state === "attention" && row.preparedOutputs
      ? "Prepared · open point"
      : row.state === "attention"
        ? "Open point"
        : row.state === "working"
          ? "Rivet is working"
          : row.state === "done"
            ? row.outcome === "edited"
              ? "Sent after edits"
              : row.outcome === "accepted"
                ? "Sent as drafted"
                : "Recorded"
            : "Prepared";
  return (
    <article className={`work-stream-row ${row.state}`}>
      <span className="work-stream-symbol">
        <Icon
          size={22}
          className={row.state === "working" ? "work-spinning" : ""}
        />
      </span>
      <div className="work-stream-content">
        <div className="work-stream-meta">
          <span>
            {row.orderNumber ||
              (row.source === "system" ? "Mailbox" : "Order correspondence")}
          </span>
        </div>
        <h3>{row.subject}</h3>
        {row.sender && <p className="work-stream-sender">{row.sender}</p>}
        <p className="work-stream-outcome">
          {row.outcomeDetail ||
            (row.nativeDraft && row.state === "attention"
              ? row.existingDraft
                ? "Your Outlook draft is preserved. Rivet’s prepared work is available with the order."
                : `Reply drafted in Outlook${row.attachments.length ? ` with ${row.attachments.length} supporting ${row.attachments.length === 1 ? "file" : "files"}` : ""}.`
              : row.detail)}
        </p>
        {(row.openPoint || (row.nativeDraft && row.state === "attention")) && (
          <p className="work-stream-open-point">
            <strong>{row.openPointTitle || "Open point"}</strong>{" "}
            {row.openPoint || row.detail}
          </p>
        )}
        <div className="work-stream-state">
          <span>{label}</span>
          {row.preparedLabel ? (
            <span>{row.preparedLabel}</span>
          ) : (
            row.preparedOutputs > 1 && (
              <span>
                {row.preparedOutputs}
                {row.preparedByAI ? " AI" : ""}{" "}
                {row.preparedOutputs === 1 ? "draft" : "drafts"} saved
              </span>
            )
          )}
          {row.origin === "ai" && (
            <span>
              {row.state === "ready" || row.preparedOutputs
                ? "AI prepared"
                : "AI preparation"}
            </span>
          )}
          {row.state === "done" && row.learningStatus === "approved" && (
            <span>Saved as an example for this order</span>
          )}
          {row.state === "done" &&
            ["recorded", "pending_review"].includes(
              row.learningStatus || "",
            ) && <span>Rivet learned from your edits</span>}
        </div>
      </div>
      <div className="work-stream-actions">
        <time dateTime={row.at || undefined}>{row.at ? when(row.at) : ""}</time>
        <OutlookDraftButton
          messageId={row.internetMessageId}
          url={row.outlookUrl}
          native={row.nativeDraft}
        />
        {row.orderId ? (
          <button
            className="work-details-link"
            onClick={() => navigate(`order/${row.orderId}`)}
          >
            {row.outcomeDetail || row.preparedOutputs
              ? "View prepared work"
              : row.state === "working"
                ? "View progress"
                : "View details"}
          </button>
        ) : row.source === "system" ? (
          <button
            className="work-details-link"
            onClick={() => navigate("settings")}
          >
            Connection settings
          </button>
        ) : null}
      </div>
    </article>
  );
}

export function AutonomyPreferences() {
  const admin = useAccount().role === "org:admin";
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const query = useQuery({
    queryKey: ["autonomy-settings"],
    queryFn: () =>
      api<{
        draft_in_outlook: boolean;
        tag_in_outlook: boolean;
        learn_from_sent: "auto" | "review";
      }>("/autopilot/settings"),
  });
  async function change(patch: object) {
    setSaving(true);
    setError("");
    try {
      await api("/autopilot/settings", patch, "PUT");
      await query.refetch();
      void qc.invalidateQueries({ queryKey: ["autopilot"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <section className="work-autonomy-settings">
      <h2>What Rivet handles</h2>
      <p>
        Rivet prepares the work automatically. You send customer replies from
        Outlook.
      </p>
      {query.isError && (
        <ErrorNote message="Could not load the team’s autonomy settings." />
      )}
      {error && <ErrorNote message={error} />}{" "}
      {query.data && (
        <div>
          {[
            {
              key: "draft_in_outlook",
              label: "Prepare replies in Outlook",
              detail:
                "Save an unsent reply in the customer’s thread, with supported released drawings.",
            },
            {
              key: "tag_in_outlook",
              label: "Label prepared replies",
              detail: "Add the Rivet category to handled messages.",
            },
            {
              key: "learn_from_sent",
              label: "Use sent replies as examples on the same order",
              detail:
                "Your edits guide future replies. Broader company guidance requires administrator approval.",
            },
          ].map((item) => (
            <label key={item.key}>
              <input
                type="checkbox"
                disabled={!admin || saving}
                checked={
                  item.key === "learn_from_sent"
                    ? query.data!.learn_from_sent === "auto"
                    : query.data![
                        item.key as "draft_in_outlook" | "tag_in_outlook"
                      ]
                }
                onChange={(e) =>
                  void change({
                    [item.key]:
                      item.key === "learn_from_sent"
                        ? e.target.checked
                          ? "auto"
                          : "review"
                        : e.target.checked,
                  })
                }
              />
              <span>
                <strong>{item.label}</strong>
                <small>{item.detail}</small>
              </span>
            </label>
          ))}
        </div>
      )}
    </section>
  );
}
