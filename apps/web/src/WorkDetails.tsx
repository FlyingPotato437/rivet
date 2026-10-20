import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowUpRight, Check, FileText } from "@phosphor-icons/react";
import { api, when } from "./api";
import { Busy, ErrorNote } from "./ui";
import type { RecordView } from "./record-types";
import type { ProjectFlow } from "./project-flow";
import { OrderEvidence } from "./OrderEvidence";
import { OutlookDraftButton } from "./OutlookDraftButton";
import { DocumentOutlookDraftButton } from "./DocumentOutlookDraftButton";
import {
  decideItem,
  type DecisionItem,
  type StoryEvent,
} from "./work-presentation";
import { ApprovalEvidence } from "./ApprovalReview";
import type { ActionExecutionView } from "./action-execution";

export function ReplyAction({
  order,
  label,
}: {
  order: ProjectFlow;
  label?: string;
}) {
  const data = useQuery({
    queryKey: ["project-work", order.id],
    queryFn: () => api<ProjectFlow>(`/project-work/${order.id}`),
    enabled: order.status === "prepared",
    staleTime: 5000,
  });
  const p = data.data || order;
  if (p.outlook.native || p.outlook.url)
    return (
      <OutlookDraftButton
        messageId={p.outlook.internet_message_id}
        url={p.outlook.url}
        native={p.outlook.native}
        label={label}
      />
    );
  if (p.reply_preparation?.prepared_reply)
    return (
      <DocumentOutlookDraftButton
        orderId={p.id}
        replyId={p.reply_preparation.prepared_reply.id}
      />
    );
  return data.isError ? (
    <ErrorNote message="The reply could not load. Try again shortly." />
  ) : (
    <Busy text="Loading reply…" />
  );
}

export function decisionLabel(item: DecisionItem) {
  return item.gate === "money"
    ? "Approve price"
    : item.gate === "scope"
      ? "Approve scope"
      : item.gate === "engineering"
        ? "Approve drawing changes"
        : "Approve response";
}

export function useDecision(item: DecisionItem, onDecided?: () => void) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [result, setResult] = useState("");
  const qc = useQueryClient();
  async function decide(decision: "approve" | "reject") {
    if (busy || result) return;
    setBusy(true);
    setError("");
    try {
      await decideItem(item, decision, api);
      setResult(decision === "approve" ? "Approved" : "Rejected");
      onDecided?.();
      await Promise.all(
        [
          "daily-brief",
          "order-lifecycle",
          "order-activity",
          "project-work",
          "approval-card",
        ].map((key) => qc.invalidateQueries({ queryKey: [key] })),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, result, decide };
}

export function DecisionCard({
  item,
  seeWhy,
  onDecided,
  compact = false,
}: {
  item: DecisionItem;
  seeWhy: () => void;
  onDecided?: () => void;
  compact?: boolean;
}) {
  const { busy, error, result, decide } = useDecision(item, onDecided);
  const label = decisionLabel(item);
  return (
    <article className={`today-decision${compact ? " is-compact" : ""}`}>
      <span className="decision-symbol">
        <Check size={18} />
      </span>
      <div className="today-decision-copy">
        <span className="story-overline">{item.number}</span>
        <h3>{item.title}</h3>
        <p>{item.reason}</p>
        {error && <ErrorNote message={error} />}{" "}
        {result && <p role="status">{result}. Your decision has been saved.</p>}
      </div>
      {!result && (
        <div className="today-decision-actions">
          <button className="text-button" onClick={seeWhy}>
            See why
          </button>
          <button
            className="primary"
            disabled={busy}
            onClick={() => void decide("approve")}
          >
            {busy ? "Saving…" : label}
          </button>
          {compact && (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void decide("reject")}
            >
              Reject
            </button>
          )}
        </div>
      )}
    </article>
  );
}

export function DecisionDetails({
  item,
  navigate,
  openSource,
}: {
  item: DecisionItem;
  navigate: (to: string) => void;
  openSource?: (id: string) => void;
}) {
  const record = useQuery({
    queryKey: ["record", item.orderId],
    queryFn: () => api<RecordView>(`/orders/${item.orderId}/record`),
  });
  return (
    <>
      <p className="drawer-intro">{item.reason}</p>
      <h3>What Rivet prepared</h3>
      <ul className="drawer-artifacts">
        {item.package.artifacts.map((a) => (
          <li key={a.name}>
            <FileText size={17} />
            <a href={a.url}>{a.name}</a>
          </li>
        ))}
      </ul>
      {item.package.payload.email_draft && (
        <div className="drawer-reading">
          <h3>{item.package.payload.email_draft.subject}</h3>
          <p>{item.package.payload.email_draft.body}</p>
        </div>
      )}
      <h3>Supporting evidence</h3>
      <ApprovalEvidence
        data={record.data}
        loading={record.isPending}
        error={record.isError}
        sourceIds={Object.keys(item.package.sources)}
        open={(id) =>
          openSource
            ? openSource(id)
            : navigate(
                `order/${item.orderId}?view=tools&source=${encodeURIComponent(id)}`,
              )
        }
      />
      <DecisionCard
        item={item}
        compact
        seeWhy={() =>
          navigate(`order/${item.orderId}?view=advanced&tab=sharing`)
        }
      />
      <p className="drawer-footnote">
        Only your named approval authority can approve this work. Sending the
        customer’s reply stays in Outlook.
      </p>
    </>
  );
}

export function SourceDetails({
  orderId,
  sourceId,
  close,
}: {
  orderId: string;
  sourceId: string;
  close: () => void;
}) {
  const q = useQuery({
    queryKey: ["record", orderId],
    queryFn: () => api<RecordView>(`/orders/${orderId}/record`),
  });
  if (q.isPending) return <Busy text="Opening source…" />;
  if (!q.data) return <ErrorNote message="This source could not load." />;
  const source = q.data.sources.find((s) => s.id === sourceId);
  const doc = q.data.documents.find(
    (d) => d.id === (source?.document_id || sourceId),
  );
  if (!doc)
    return (
      <ErrorNote message="This source is not available in the current order." />
    );
  return (
    <OrderEvidence
      key={sourceId}
      inline
      document={doc}
      sources={q.data.sources.filter((s) => s.document_id === doc.id)}
      selectedId={source?.id}
      close={close}
    />
  );
}

export function StoryDetails({
  event,
  order,
  openSource,
}: {
  event: StoryEvent;
  order: ProjectFlow;
  openSource: (id: string) => void;
}) {
  const prep = order.reply_preparation;
  if (event.execution)
    return (
      <ConnectedExecutionDetails
        orderId={order.id}
        provided={event.execution}
      />
    );
  if (event.earlier)
    return (
      <>
        <p>
          The latest prepared response is shown on the order timeline. These
          earlier attempts remain in the history.
        </p>
        {event.earlier.map((e) => (
          <section key={e.id}>
            <h3>{when(e.at)}</h3>
            <div className="drawer-reading">
              <Markdown
                remarkPlugins={[remarkGfm]}
                disallowedElements={["img"]}
              >
                {e.detail || e.title}
              </Markdown>
            </div>
          </section>
        ))}
      </>
    );
  if (event.reply)
    return (
      <>
        <ReplyAction order={order} />
        {prep?.prepared_reply ? (
          <>
            <div className="drawer-reading">
              <Markdown
                remarkPlugins={[remarkGfm]}
                disallowedElements={["img"]}
              >
                {prep.prepared_reply.body}
              </Markdown>
            </div>
            {!!prep.prepared_reply.unresolved.length && (
              <>
                <h3>Still open</h3>
                <ul>
                  {prep.prepared_reply.unresolved.map((text, i) => (
                    <li key={i}>{text}</li>
                  ))}
                </ul>
              </>
            )}
            <h3>Sources Rivet used</h3>
            <div className="story-source-chips">
              {prep.prepared_reply.source_refs.map((s) => (
                <button key={s.id} onClick={() => openSource(s.id)}>
                  {s.label}
                  <ArrowUpRight size={13} />
                </button>
              ))}
            </div>
          </>
        ) : (
          <p>The latest reply will appear here when preparation is complete.</p>
        )}
      </>
    );
  if (event.email)
    return (
      <>
        <dl className="drawer-facts">
          <dt>From</dt>
          <dd>{event.email.sender || "Your team"}</dd>
          <dt>When</dt>
          <dd>{when(event.email.at)}</dd>
        </dl>
        {event.email.outlook_url && (
          <a
            className="story-text-link"
            href={event.email.outlook_url}
            target="_blank"
            rel="noreferrer"
          >
            Open in Outlook <ArrowUpRight size={14} />
          </a>
        )}
        <div className="drawer-reading">
          <p>
            {event.email.body ||
              "Open the original email in Outlook to read the full message."}
          </p>
        </div>
        {!!event.email.attachments?.length && (
          <ul className="drawer-artifacts">
            {event.email.attachments.map((f, i) => (
              <li key={i}>
                <FileText size={16} />
                {f.name || f.filename}
              </li>
            ))}
          </ul>
        )}
      </>
    );
  return (
    <>
      {event.raw?.detail && (
        <div className="drawer-reading">
          <Markdown remarkPlugins={[remarkGfm]} disallowedElements={["img"]}>
            {event.raw.detail}
          </Markdown>
        </div>
      )}
      {event.package && (
        <>
          <p>
            {event.package.status === "approved"
              ? "This work has the recorded approvals."
              : "Prepared changes. Approval and publication are separate steps."}
          </p>
          <ul className="drawer-artifacts">
            {event.package.artifacts.map((a) => (
              <li key={a.name}>
                <FileText size={16} />
                <a href={a.url}>{a.name}</a>
              </li>
            ))}
          </ul>
          <h3>Details</h3>
          <dl className="drawer-facts">
            <dt>Environment</dt>
            <dd>
              {event.package.mode.includes("local") ||
              event.package.mode.includes("test")
                ? "Test environment"
                : event.package.mode}
            </dd>
            <dt>Source version</dt>
            <dd className="drawer-code">{event.package.fingerprint}</dd>
          </dl>
          {!!event.package.receipts.length && (
            <>
              <h3>Connected systems</h3>
              <ul>
                {event.package.receipts.map((r, i) => (
                  <li key={i}>
                    <strong>{r.system}</strong> · {r.action} · {r.status}
                    <p>{r.notice}</p>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
      {!!event.sourceIds?.length && (
        <>
          <h3>Sources</h3>
          <div className="story-source-chips">
            {event.sourceIds.map((id, i) => (
              <button key={id} onClick={() => openSource(id)}>
                Source {i + 1}
                <ArrowUpRight size={13} />
              </button>
            ))}
          </div>
        </>
      )}
      {event.raw?.outlook_url && (
        <a href={event.raw.outlook_url} target="_blank" rel="noreferrer">
          Open in Outlook
        </a>
      )}
    </>
  );
}

export function ConnectedExecutionDetails({
  orderId,
  provided,
}: {
  orderId: string;
  provided?: ActionExecutionView;
}) {
  const q = useQuery({
    queryKey: ["workflow-actions", orderId],
    queryFn: () =>
      api<ActionExecutionView>(`/orders/${orderId}/workflow-actions`),
    enabled: !provided,
  });
  const data = provided || q.data;
  if (!data?.attempts.length)
    return q.isError ? (
      <ErrorNote message="Connected-system details could not load." />
    ) : null;
  return (
    <section className="audit-package">
      <h3>Work across connected systems</h3>
      {!data.vendor_writes && (
        <span className="audit-test-badge">Test environment</span>
      )}
      {data.attempts.map((a) => (
        <article className="audit-package" key={a.id}>
          <h4>{a.action}</h4>
          <dl className="drawer-facts">
            <dt>Status</dt>
            <dd>{a.status}</dd>
            <dt>When</dt>
            <dd>{when(a.receipt?.applied_at || a.at)}</dd>
            <dt>Action ID</dt>
            <dd className="drawer-code">{a.action_id}</dd>
            {a.receipt && (
              <>
                <dt>Remote version</dt>
                <dd>{a.receipt.remote_version}</dd>
                <dt>Source version</dt>
                <dd className="drawer-code">{a.receipt.source_state_sha256}</dd>
                <dt>Approved work</dt>
                <dd className="drawer-code">{a.receipt.package_fingerprint}</dd>
              </>
            )}
          </dl>
          {a.message && <p>{a.message}</p>}
          {a.receipt?.files.map((f) => (
            <p className="drawer-code" key={f.document_id}>
              {f.name} · SHA-256 {f.sha256}
            </p>
          ))}
        </article>
      ))}
    </section>
  );
}
