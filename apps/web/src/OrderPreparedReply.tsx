import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowUpRight,
  CircleNotch,
  EnvelopeSimple,
} from "@phosphor-icons/react";
import { api } from "./api";
import { RivetIcon } from "./ui";
import { OutlookDraftButton } from "./OutlookDraftButton";
import { DocumentOutlookDraftButton } from "./DocumentOutlookDraftButton";
import { ReplyLearning, type LearningContext } from "./CompanyLearning";

export type Preparation = {
  internet_message_id?: string;
  outlook_draft?: { status: string } | null;
  preparation: {
    status:
      | "queued"
      | "running"
      | "ready"
      | "blocked"
      | "failed"
      | "reviewed"
      | "not_needed";
    detail: string;
    automatic: boolean;
  };
  email_subject: string | null;
  outlook_url: string | null;
  delivery?: "document";
  prepared_reply: null | {
    created_at?: string;
    learning_context?: LearningContext;
    id: string;
    body: string;
    unresolved: string[];
    source_refs: { id: string; label: string; url: string }[];
    coverage: { documents_read: number; documents_total?: number };
  };
};

/** A view of completed work. Opening the dashboard never creates or sends a reply. */
export function OrderPreparedReply({
  orderId,
  expanded = false,
  openSource,
  provided,
  showOutlookAction = true,
}: {
  orderId: string;
  expanded?: boolean;
  openSource?: (id: string) => void;
  provided?: Preparation;
  showOutlookAction?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  const query = useQuery({
    queryKey: ["order-reply-preparation", orderId],
    queryFn: () => api<Preparation>(`/orders/${orderId}/reply-preparation`),
    refetchInterval: 8000,
    enabled: !provided,
  });
  const data = provided || query.data;
  if (!provided && query.isError)
    return (
      <div className="order-reply-unavailable">
        Reply status is unavailable.{" "}
        <button className="text-button" onClick={() => void query.refetch()}>
          Try again
        </button>
      </div>
    );
  if (!data || data.preparation.status === "not_needed") return null;
  const state = data.preparation.status;
  const sent = data.outlook_draft?.status === "sent";
  const working = state === "queued" || state === "running";
  const reply = ["ready", "reviewed"].includes(state)
    ? data.prepared_reply
    : null;
  const oneSourceFile =
    reply &&
    new Set(reply.source_refs.map((s) => s.label.split(" · p.")[0])).size === 1;
  return (
    <section
      className="order-prepared-reply"
      aria-label="Rivet’s prepared reply"
    >
      <header>
        <span className="order-reply-symbol">
          <RivetIcon name="agent" size={23} />
        </span>
        <div>
          <span className="order-reply-attribution">
            {data.preparation.automatic
              ? "Prepared automatically by Rivet"
              : "Rivet’s work"}{" "}
            · AI
          </span>
          <h2>
            {sent
              ? "Reply sent from Outlook"
              : reply
                ? state === "reviewed"
                  ? "Your reviewed reply"
                  : data.delivery === "document"
                    ? "Response prepared"
                    : "Your reply is prepared"
                : working
                  ? "Rivet is preparing your reply"
                  : "Reply needs attention"}
          </h2>
          {data.email_subject && <p>{data.email_subject}</p>}
        </div>
        {working && (
          <CircleNotch
            className="is-working"
            size={21}
            aria-label="Preparing"
          />
        )}
        {showOutlookAction &&
          (data.outlook_url ||
            ["created", "existing_draft"].includes(
              data.outlook_draft?.status || "",
            )) && (
            <OutlookDraftButton
              url={data.outlook_url}
              messageId={data.internet_message_id}
              native={["created", "existing_draft"].includes(
                data.outlook_draft?.status || "",
              )}
            />
          )}
      </header>
      {reply ? (
        <>
          <ReplyLearning context={reply.learning_context} />
          {data.delivery === "document" && (
            <div className="order-reply-actions">
              <DocumentOutlookDraftButton
                orderId={orderId}
                replyId={reply.id}
              />
              <button
                className="secondary"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(reply.body);
                    setCopied(true);
                    setCopyError("");
                  } catch {
                    setCopyError(
                      "Could not copy. Select the prepared text below.",
                    );
                  }
                }}
              >
                {copied ? "Copied response" : "Copy response"}
              </button>
              <span>Draft for your review · not sent</span>
              {copyError && <p role="alert">{copyError}</p>}
            </div>
          )}
          {!!reply.unresolved.length && (
            <details className="order-reply-open-points">
              <summary>
                {reply.unresolved.length}{" "}
                {reply.unresolved.length === 1 ? "open point" : "open points"}{" "}
                to be aware of
              </summary>
              <ul>
                {reply.unresolved.map((point, index) => (
                  <li key={index}>{point}</li>
                ))}
              </ul>
            </details>
          )}
          <details className="order-reply-preview" open={expanded || undefined}>
            <summary>
              Read prepared reply{" "}
              <span>{reply.coverage.documents_read} documents consulted</span>
            </summary>
            <div className="order-reply-body">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                disallowedElements={["img"]}
                components={{ a: ({ children }) => <span>{children}</span> }}
              >
                {reply.body}
              </ReactMarkdown>
            </div>
            {!!reply.source_refs.length && (
              <details className="order-reply-sources">
                <summary>
                  Supporting sources · {reply.source_refs.length}
                </summary>
                <ul>
                  {reply.source_refs.map((source, index) => {
                    const label =
                      oneSourceFile && source.label.includes(" · p.")
                        ? `p.${source.label.split(" · p.").slice(1).join(" · p.")}`
                        : source.label;
                    let href = "";
                    try {
                      const hash = new URL(source.url, window.location.origin)
                        .hash;
                      if (/^#order\/[a-f0-9-]{36}(?:\?|$)/i.test(hash))
                        href = hash;
                    } catch {
                      /* Keep an invalid reference readable without making it a link. */
                    }
                    return (
                      <li key={`${source.id}:${index}`}>
                        {openSource && href.startsWith(`#order/${orderId}?`) ? (
                          <button
                            className="text-button"
                            title={source.label}
                            onClick={() => openSource(source.id)}
                          >
                            {label}
                          </button>
                        ) : href ? (
                          <a href={href} title={source.label}>
                            {label}
                          </a>
                        ) : (
                          label
                        )}
                      </li>
                    );
                  })}
                </ul>
              </details>
            )}
            <footer>
              <span>
                {reply.coverage.documents_read}{" "}
                {reply.coverage.documents_read === 1 ? "document" : "documents"}{" "}
                consulted · {reply.source_refs.length} source references
              </span>
              <span>
                {sent
                  ? "Sent from Outlook. This preview keeps Rivet’s prepared wording; your team’s edits are recorded in the order history."
                  : data.outlook_draft?.status === "existing_draft"
                    ? "Your Outlook draft is preserved; this is Rivet’s prepared wording."
                    : data.delivery === "document"
                      ? "Prepared from uploaded documents. No mailbox thread is connected."
                      : "Review and send from Outlook."}
              </span>
            </footer>
          </details>
        </>
      ) : (
        <p className="order-reply-progress" role="status">
          {data.preparation.detail}
        </p>
      )}
    </section>
  );
}
