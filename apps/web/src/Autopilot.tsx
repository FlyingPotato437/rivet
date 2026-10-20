import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  CheckCircle,
  CaretDown,
  EnvelopeSimple,
  Paperclip,
  SlidersHorizontal,
  Sparkle,
  WarningCircle,
} from "@phosphor-icons/react";
import { api } from "./api";
import { useAccount } from "./Auth";
import { safeOutlookUrl } from "./outlook-workflow";
import { Busy, ErrorNote, Modal } from "./ui";
import "./autopilot.css";

type Settings = {
  draft_in_outlook: boolean;
  tag_in_outlook: boolean;
  learn_from_sent: "auto" | "review";
  updated_by: string;
  updated_at: string | null;
};
type Mail = { subject: string; sender: string; outlook_url: string };
type FeedItem = Mail & {
  at: string | null;
  kind: "read" | "drafted" | "sent";
  order: string | null;
  detail: string;
};
type Waiting = Mail & {
  order: string | null;
  drafted_at: string | null;
  attachments: string[];
  origin?: "rivet" | "existing";
};
type NeedsYou = Mail & { kind: string; order: string | null; detail: string };
type Activity = {
  days: number;
  summary: {
    emails_read: number;
    filed_to_orders: number;
    set_aside: number;
    replies_prepared: number;
    drafted_in_outlook: number;
    sent_unchanged: number;
    sent_edited: number;
    learned: number;
    needs_you: number;
  };
  waiting_in_outlook: Waiting[];
  needs_you: NeedsYou[];
  feed: FeedItem[];
  settings: Settings;
};

function ago(value: string | null) {
  if (!value) return "";
  const minutes = Math.round((Date.now() - new Date(value).getTime()) / 60000);
  if (Number.isNaN(minutes)) return "";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(value).toLocaleDateString([], {
    month: "short",
    day: "numeric",
  });
}

function OutlookLink({ url }: { url: string }) {
  const safeUrl = safeOutlookUrl(url);
  if (!safeUrl) return null;
  return (
    <a className="ap-outlook" href={safeUrl} target="_blank" rel="noreferrer">
      Open in Outlook <ArrowUpRight size={13} />
    </a>
  );
}

const FEED_ICON = {
  read: EnvelopeSimple,
  drafted: Sparkle,
  sent: CheckCircle,
};

export default function Autopilot() {
  const admin = useAccount().role === "org:admin";
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [expandedActivity, setExpandedActivity] = useState(false);
  const query = useQuery({
    queryKey: ["autopilot"],
    queryFn: () => api<Activity>("/autopilot/activity"),
    refetchInterval: 30000,
  });
  async function change(patch: Partial<Settings>) {
    if (!admin || saving) return;
    setSaving(Object.keys(patch)[0]);
    setError("");
    try {
      await api<Settings>("/autopilot/settings", patch, "PUT");
      await query.refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving("");
    }
  }
  if (query.isLoading)
    return (
      <main className="page autopilot-page">
        <Busy text="Checking what Rivet did…" />
      </main>
    );
  if (!query.data)
    return (
      <main className="page autopilot-page">
        <ErrorNote message="Rivet could not load its activity. Check your connection and try again." />
        <button className="secondary" onClick={() => void query.refetch()}>
          Try again
        </button>
      </main>
    );
  const { summary, settings } = query.data;
  const sent = summary.sent_unchanged + summary.sent_edited;
  const permissionNeeded = query.data.needs_you.some(
    (item) => item.kind === "permission_required",
  );
  const activity = query.data.feed.filter(
    (item) => item.kind !== "read" || item.order,
  );
  const visibleActivity = expandedActivity ? activity : activity.slice(0, 5);
  return (
    <main className="page autopilot-page">
      <header className="ap-heading">
        <div>
          <h1>Rivet’s work</h1>
          <p>Your replies in Outlook. The details here when you need them.</p>
        </div>
        <button className="secondary" onClick={() => setSettingsOpen(true)}>
          <SlidersHorizontal size={17} /> Autopilot settings
        </button>
      </header>
      {query.isError && (
        <ErrorNote message="Could not refresh. Showing the last saved activity." />
      )}
      {query.data.needs_you.length > 0 && (
        <section className="ap-attention" aria-label="Needs your attention">
          <h2>
            <WarningCircle size={19} /> Needs your attention{" "}
            <span>{query.data.needs_you.length}</span>
          </h2>
          <ul className="ap-list">
            {query.data.needs_you.map((n, i) => (
              <li key={i}>
                <div>
                  <strong>
                    {n.kind === "permission_required"
                      ? "Allow Rivet to save Outlook drafts"
                      : n.subject || "Mailbox connection"}
                  </strong>
                  <p>{n.detail}</p>
                </div>
                {n.kind === "permission_required" ? (
                  <a className="ap-outlook" href="#settings">
                    Mailbox settings <ArrowUpRight size={15} />
                  </a>
                ) : n.kind === "which_job" ? (
                  <a className="ap-outlook" href="#review">
                    Confirm the order
                  </a>
                ) : (
                  <OutlookLink url={n.outlook_url} />
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="ap-drafts" aria-label="Ready in Outlook">
        <div className="ap-section-heading">
          <div className="ap-drafts-heading">
            <span className="ap-mail-symbol">
              <EnvelopeSimple size={22} aria-hidden="true" />
            </span>
            <div>
              <h2>
                Ready in Outlook{" "}
                <span className="ap-count">
                  {query.data.waiting_in_outlook.length}
                </span>
              </h2>
              <p>Review, edit, and send from your inbox.</p>
            </div>
          </div>
        </div>
        {query.data.waiting_in_outlook.length === 0 ? (
          <div className="ap-empty-drafts">
            <EnvelopeSimple size={28} aria-hidden="true" />
            <h3>No replies waiting</h3>
            <p>
              {permissionNeeded
                ? "No replies placed yet. Update the mailbox permission so prepared replies can appear automatically."
                : !settings.draft_in_outlook
                  ? "Automatic Outlook drafts are off. Prepared responses remain in their order records."
                  : "New replies will appear here when they’re saved in Outlook."}
            </p>
            {!summary.emails_read && (
              <a href="#settings">Connect your mailbox</a>
            )}
          </div>
        ) : (
          <ul className="ap-list">
            {query.data.waiting_in_outlook.map((w, i) => (
              <li key={i}>
                <div>
                  <span className="ap-origin">
                    {w.origin === "existing"
                      ? "Your existing draft"
                      : "AI draft · Prepared by Rivet"}
                  </span>
                  <strong>{w.subject || "Customer email"}</strong>
                  <div className="ap-mail-meta">
                    {w.order && <span>{w.order}</span>}
                    {w.sender && <span>{w.sender}</span>}
                    {w.drafted_at && (
                      <time dateTime={w.drafted_at}>{ago(w.drafted_at)}</time>
                    )}
                  </div>
                  {w.attachments.length > 0 && (
                    <details className="ap-files">
                      <summary>
                        <Paperclip size={14} />
                        {w.attachments.length}{" "}
                        {w.attachments.length === 1
                          ? "attachment"
                          : "attachments"}
                      </summary>
                      <ul>
                        {w.attachments.map((file, index) => (
                          <li key={index}>{file}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
                <OutlookLink
                  url={
                    w.outlook_url || "https://outlook.office.com/mail/drafts"
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </section>
      <div className="ap-review-link">
        <span>Questions and decisions across your orders</span>
        <a href="#review">
          Open review queue <ArrowUpRight size={15} />
        </a>
      </div>
      <section className="ap-activity" aria-label="Recent activity">
        <div className="ap-section-heading">
          <div>
            <h2>Recent activity</h2>
            <p>Last {query.data.days} days</p>
          </div>
        </div>
        {visibleActivity.length === 0 ? (
          <p className="ap-empty">
            {summary.emails_read
              ? "No order activity yet. Updates will appear here when Rivet works on an order."
              : "No mail captured yet. Check your mailbox connection in settings."}
          </p>
        ) : (
          <ol className="ap-feed">
            {visibleActivity.map((item, i) => {
              const Icon = FEED_ICON[item.kind] ?? EnvelopeSimple;
              return (
                <li key={i} className={`ap-${item.kind}`}>
                  <Icon size={16} />
                  <div>
                    <strong>{item.detail}</strong>
                    <span>
                      {item.subject}
                      {item.order && !item.subject.includes(item.order)
                        ? ` · ${item.order}`
                        : ""}
                    </span>
                  </div>
                  <time dateTime={item.at || undefined}>{ago(item.at)}</time>
                </li>
              );
            })}
          </ol>
        )}
        {activity.length > 5 && (
          <button
            className="text-button ap-more"
            onClick={() => setExpandedActivity(!expandedActivity)}
          >
            {expandedActivity
              ? "Show less"
              : `Show ${activity.length - 5} more`}
          </button>
        )}
      </section>
      <details className="ap-report">
        <summary>
          Activity summary <CaretDown size={15} />
        </summary>
        <dl>
          <div>
            <dt>Emails read</dt>
            <dd>
              {summary.emails_read}{" "}
              <small>
                {summary.filed_to_orders} filed · {summary.set_aside} set aside
              </small>
            </dd>
          </div>
          <div>
            <dt>Replies prepared</dt>
            <dd>
              {summary.replies_prepared}{" "}
              <small>{summary.drafted_in_outlook} saved to Outlook</small>
            </dd>
          </div>
          <div>
            <dt>Sent by your team</dt>
            <dd>
              {sent}{" "}
              <small>
                {summary.sent_unchanged} unchanged · {summary.sent_edited}{" "}
                edited
              </small>
            </dd>
          </div>
          <div>
            <dt>Saved reply examples</dt>
            <dd>
              {summary.learned} <small>For the same order</small>
            </dd>
          </div>
        </dl>
      </details>
      {settingsOpen && (
        <Modal
          title="Autopilot settings"
          onClose={() => setSettingsOpen(false)}
        >
          <section className="ap-settings" aria-label="Autopilot settings">
            <p className="ap-empty">
              Rivet never sends mail to a customer and never approves
              engineering or commercial changes.{" "}
              {admin
                ? "These settings apply to your team."
                : "A team administrator manages these settings."}
            </p>
            {error && <ErrorNote message={error} />}
            <label>
              <input
                type="checkbox"
                checked={settings.draft_in_outlook}
                disabled={!admin || !!saving}
                onChange={(e) => change({ draft_in_outlook: e.target.checked })}
              />
              <span>
                <strong>Put prepared replies in Outlook</strong>
                Places an unsent reply in the customer's thread, with relevant
                released drawings when available.
              </span>
            </label>
            <label>
              <input
                type="checkbox"
                checked={settings.tag_in_outlook}
                disabled={!admin || !!saving}
                onChange={(e) => change({ tag_in_outlook: e.target.checked })}
              />
              <span>
                <strong>Label emails Rivet has handled</strong>
                Adds the “Rivet: reply drafted” category in your inbox.
              </span>
            </label>
            <label>
              <input
                type="checkbox"
                checked={settings.learn_from_sent === "auto"}
                disabled={!admin || !!saving}
                onChange={(e) =>
                  change({
                    learn_from_sent: e.target.checked ? "auto" : "review",
                  })
                }
              />
              <span>
                <strong>Learn from sent replies on the same order</strong>
                Sent replies and edits guide future replies on that order. Off
                means an administrator approves each example. Broader company
                guidance always needs administrator approval.
              </span>
            </label>
          </section>
        </Modal>
      )}
    </main>
  );
}
