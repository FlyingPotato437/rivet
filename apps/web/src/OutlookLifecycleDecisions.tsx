import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Check,
  DownloadSimple,
  FileText,
  X,
} from "@phosphor-icons/react";
import { api, apiFetch } from "./api";
import type { MessageContext } from "./outlook-bridge";
import { workspaceSourceUrl } from "./outlook-workflow";
import {
  gateLabels,
  draftBundles,
  lifecycleActorLabel,
  lifecycleArtifactPath,
  lifecycleCommercialStatus,
  lifecycleEvidenceFields,
  lifecycleGateEvidence,
  lifecycleHandoffStatus,
  type LifecycleArtifact,
  type LifecycleGate,
} from "./lifecycle-work";
import {
  outlookLifecycleMatches,
  outlookPackageSources,
  outlookLifecycleDecision,
  outlookPackageHandoff,
  type OutlookLifecyclePackage,
  type OutlookLifecycleSource,
  type OutlookLifecycleView,
} from "./outlook-lifecycle";
import "./outlook-lifecycle.css";

const endpoint = (id: string) =>
  `/reply-assistant/lifecycle?internet_message_id=${encodeURIComponent(id)}`;

export function useOutlookLifecycle(
  message: MessageContext | null,
  orderId: string,
) {
  return useQuery({
    queryKey: [
      "outlook-lifecycle",
      message?.itemKey,
      message?.internetMessageId,
      orderId,
    ],
    queryFn: () =>
      api<OutlookLifecycleView>(endpoint(message!.internetMessageId)),
    enabled:
      message?.mode === "outlook" &&
      !!message.itemKey &&
      !!message.internetMessageId &&
      !!orderId,
    refetchInterval: 8000,
    retry: false,
  });
}

type Props = {
  message: MessageContext;
  orderId: string;
  query: ReturnType<typeof useOutlookLifecycle>;
  readCurrentMessage: () => MessageContext;
  workspaceBase: string;
  onOpenNativeDraft: () => void;
};

export function OutlookLifecycleDecisions(props: Props) {
  const { query, message, orderId } = props;
  const view = query.data;
  if (!outlookLifecycleMatches(view, message, orderId)) return null;
  const packages = view!.packages.filter(
    (item) => !["rejected", "superseded"].includes(item.status),
  );
  if (!packages.length) return null;
  return (
    <section className="ol-review" aria-label="Prepared work for this email">
      <header>
        <span>Prepared for this email</span>
        <h2>Review the finished work</h2>
        <p>
          Decisions stay here in Outlook. Rivet prepares the draft; you send it.
        </p>
      </header>
      {query.isError && (
        <p className="ol-error" role="alert">
          Could not verify the latest package.{" "}
          <button type="button" onClick={() => void query.refetch()}>
            Refresh
          </button>
        </p>
      )}
      {packages.map((item) => (
        <PackageReview key={item.id} {...props} view={view!} item={item} />
      ))}
    </section>
  );
}

function PackageReview({
  item,
  view,
  ...props
}: Props & { view: OutlookLifecycleView; item: OutlookLifecyclePackage }) {
  const [gate, setGate] = useState<LifecycleGate | null>(null);
  const handoff = lifecycleHandoffStatus({
    ...item,
    outlook_handoff: outlookPackageHandoff(item, view),
  });
  const counts = item.payload.counts;
  const price = item.payload.change_order?.total ?? item.payload.total;
  const currency =
    item.payload.change_order?.lines[0]?.currency ||
    item.payload.currency ||
    "USD";
  const priceText =
    price !== undefined
      ? new Intl.NumberFormat("en-US", { style: "currency", currency }).format(
          Number(price),
        )
      : "";
  const pdfs = item.artifacts.filter(
    (file) =>
      file.media_type === "application/pdf" &&
      [
        "quote.pdf",
        "response-letter.pdf",
        "change-order.pdf",
        "drafting-list.pdf",
        "handover-index.pdf",
      ].includes(file.name),
  );
  const bundle = item.artifacts.find(
    (file) =>
      file.name === "handover-documents.zip" &&
      file.media_type === "application/zip",
  );
  const ready =
    !props.query.isError &&
    item.evidence_current &&
    !item.blockers.length &&
    item.status === "awaiting_decision";
  const selectedSources = outlookPackageSources(view, item);
  return (
    <article className="ol-package">
      <div className="ol-package-heading">
        <h3>
          {(
            {
              quote: "Quote and exceptions",
              kickoff: "Purchase order and kickoff",
              revision_review: `Revision ${item.payload.revision || view.revision} response`,
              purchasing: "Material and schedule update",
              revision_check: `Revision ${item.payload.revision || view.revision} check`,
              release: "Production release package",
              closeout: "Handover documents",
            } as Record<string, string>
          )[item.operation] || item.title}
        </h3>
        <span>
          {item.mode === "local_demo"
            ? "Sample order"
            : "Saved source evidence"}
        </span>
      </div>
      {counts && (
        <p className="ol-outcome">
          {counts.answer} answers drafted · {counts.text_edit} fields edited
          {counts.layout_task
            ? ` · ${counts.layout_task} item${counts.layout_task === 1 ? "" : "s"} for drafting`
            : ""}
        </p>
      )}
      {priceText && (
        <div className="ol-price">
          <strong>{priceText}</strong>
          <span>
            {item.operation === "quote"
              ? "Proposed quote"
              : "Proposed scope addition"}
            <small>{lifecycleCommercialStatus(item)}</small>
          </span>
        </div>
      )}
      {item.payload.purchase_order_number && (
        <p className="ol-caption">
          Purchase order {item.payload.purchase_order_number}
        </p>
      )}
      {item.payload.total_comments !== undefined && (
        <p className="ol-outcome">
          {item.payload.documented_resolutions || 0} of{" "}
          {item.payload.total_comments} comments documented
          {item.payload.bom_matches
            ? " · BOM rows match"
            : " · BOM needs review"}
        </p>
      )}
      {item.payload.handover && (
        <p className="ol-caption">{item.payload.handover}</p>
      )}
      <div className="ol-files" aria-label="Prepared PDF documents">
        {pdfs.map((file) => (
          <PreparedFile
            key={file.name}
            file={file}
            orderId={props.orderId}
            packageId={item.id}
          />
        ))}
        {bundle && (
          <PreparedFile
            file={bundle}
            orderId={props.orderId}
            packageId={item.id}
          />
        )}
      </div>
      {item.payload.email_draft && (
        <details className="ol-response">
          <summary>
            <FileText size={16} /> Read the prepared response
          </summary>
          <div>
            <strong>{item.payload.email_draft.subject}</strong>
            <p>{item.payload.email_draft.body}</p>
          </div>
        </details>
      )}
      <div className={`ol-handoff ${handoff.tone}`} role="status">
        <strong>{handoff.title}</strong>
        <p>{handoff.detail}</p>
        {handoff.canOpen && (
          <button type="button" onClick={props.onOpenNativeDraft}>
            Open draft in Outlook <ArrowUpRight size={15} />
          </button>
        )}
      </div>
      {item.blockers.length > 0 && (
        <div className="ol-hold">
          <strong>Needs resolving before approval</strong>
          {item.blockers.map((text) => (
            <p key={text}>{text}</p>
          ))}
        </div>
      )}
      {!item.evidence_current && (
        <p className="ol-error">
          The sources changed. Rivet must prepare a current package before you
          approve it.
        </p>
      )}
      {item.pending_gates.length > 0 &&
        item.evidence_current &&
        item.blockers.length === 0 && (
          <div className="ol-gates">
            <h4>Specific decisions</h4>
            {item.pending_gates.map((next) => {
              const authorized = item.allowed_gates.includes(next);
              return (
                <div key={next}>
                  <button
                    type="button"
                    disabled={!ready || !authorized}
                    aria-expanded={gate === next}
                    onClick={() => setGate(gate === next ? null : next)}
                  >
                    {gateLabels[next]}
                  </button>
                  {!authorized && (
                    <small>Assigned company approver required</small>
                  )}
                </div>
              );
            })}
          </div>
        )}
      {gate && ready && item.allowed_gates.includes(gate) && (
        <PaneDecision
          key={`${item.id}:${gate}`}
          {...props}
          view={view}
          item={item}
          gate={gate}
          sources={selectedSources}
          onClose={() => setGate(null)}
        />
      )}
      {item.decisions.length > 0 && (
        <details className="ol-decisions">
          <summary>
            {item.decisions.length} recorded decision
            {item.decisions.length === 1 ? "" : "s"}
          </summary>
          {item.decisions.map((decision) => (
            <p key={decision.gate}>
              <Check size={14} />
              <span>
                {gateLabels[decision.gate]} ·{" "}
                {decision.decision === "approve" ? "approved" : "rejected"}
                <small title={decision.actor}>
                  {lifecycleActorLabel(decision.actor)} · {decision.reason}
                </small>
              </span>
            </p>
          ))}
        </details>
      )}
    </article>
  );
}

function PreparedFile({
  file,
  orderId,
  packageId,
}: {
  file: LifecycleArtifact;
  orderId: string;
  packageId: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const path = lifecycleArtifactPath(orderId, "artifacts", packageId, file);
  async function download() {
    if (!path || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(path);
      if (!response.ok)
        throw new Error(
          "This file could not be loaded. Refresh the package and try again.",
        );
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength > 32 * 1024 * 1024)
        throw new Error("This file is too large to open in the Outlook pane.");
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const actual = [...new Uint8Array(digest)]
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
      if (actual !== file.sha256.toLowerCase())
        throw new Error(
          "The file changed. Refresh this package before using it.",
        );
      if (!active.current) return;
      const url = URL.createObjectURL(
        new Blob([bytes], { type: file.media_type }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = file.name;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      if (active.current) setError((e as Error).message);
    } finally {
      if (active.current) setBusy(false);
    }
  }
  return (
    <div>
      <button
        type="button"
        disabled={!path || busy}
        onClick={() => void download()}
      >
        <DownloadSimple size={16} />
        <span>
          {busy
            ? "Opening file…"
            : (
                {
                  "quote.pdf": "Quote and exceptions",
                  "response-letter.pdf": "Response letter",
                  "change-order.pdf": "Change order",
                  "drafting-list.pdf": "Changes for drafting",
                  "handover-index.pdf": "Handover index",
                  "handover-documents.zip": "Handover document bundle",
                } as Record<string, string>
              )[file.name] || file.name}
        </span>
      </button>
      {error && (
        <p className="ol-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function PaneDecision({
  item,
  view,
  gate,
  sources,
  onClose,
  ...props
}: Props & {
  item: OutlookLifecyclePackage;
  view: OutlookLifecycleView;
  gate: LifecycleGate;
  sources: OutlookLifecycleSource[];
  onClose: () => void;
}) {
  const [snapshot] = useState(view);
  const [reason, setReason] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<{ body: string; key: string } | null>(null);
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const grouped = lifecycleGateEvidence(
    item,
    gate,
    sources.map((source) => source.id),
  );
  const preferredRoles = new Set(
    gate === "money"
      ? ["erp_price", "erp_order"]
      : gate === "scope"
        ? ["accepted_exception", "purchase_order", "customer_approval"]
        : gate === "engineering"
          ? ["drawing", "as_built", "specification", "test_report"]
          : ["email", "markup", "customer_approval"],
  );
  const first = [
    ...(grouped.relevant.length ? grouped.relevant : grouped.additional),
  ]
    .sort(
      (a, b) =>
        Number(
          preferredRoles.has(
            sources.find((source) => source.id === b)?.role || "",
          ),
        ) -
        Number(
          preferredRoles.has(
            sources.find((source) => source.id === a)?.role || "",
          ),
        ),
    )
    .slice(0, 3);
  const rest = [...grouped.relevant, ...grouped.additional].filter(
    (id) => !first.includes(id),
  );
  const changed =
    snapshot.version !== view.version ||
    snapshot.packages.find((entry) => entry.id === item.id)?.fingerprint !==
      item.fingerprint;
  async function decide(decision: "approve" | "reject") {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const current = props.readCurrentMessage();
      if (
        current.itemKey !== props.message.itemKey ||
        current.internetMessageId !== props.message.internetMessageId
      )
        throw new Error(
          "The selected email changed. Reopen this decision for the current email.",
        );
      const fresh = await api<OutlookLifecycleView>(
        endpoint(props.message.internetMessageId),
      );
      if (!active.current) return;
      const body = outlookLifecycleDecision({
        initial: snapshot,
        fresh,
        expectedMessage: props.message,
        currentMessage: props.readCurrentMessage(),
        orderId: props.orderId,
        packageId: item.id,
        gate,
        decision,
        reason,
        selectedSourceIds: selected,
      });
      const serialized = JSON.stringify(body);
      if (request.current?.body !== serialized)
        request.current = { body: serialized, key: crypto.randomUUID() };
      await api(
        `/reply-assistant/lifecycle/decisions/${item.id}`,
        body,
        "POST",
        { idempotencyKey: request.current.key },
      );
      if (!active.current) return;
      await props.query.refetch();
      if (active.current) onClose();
    } catch (e) {
      if (active.current) setError((e as Error).message);
    } finally {
      if (active.current) setBusy(false);
    }
  }
  function evidence(id: string) {
    const source = sources.find((entry) => entry.id === id);
    if (!source) return null;
    const fields = lifecycleEvidenceFields(source.text);
    const sourceUrl = workspaceSourceUrl(
      `#order/${props.orderId}?tab=work&source=${source.id}`,
      props.workspaceBase,
      { orderId: props.orderId, sourceId: source.id },
    );
    return (
      <div className="ol-evidence" key={id}>
        <details>
          <summary>
            {source.document_name}
            <small>
              {source.location.page !== undefined
                ? `Page ${source.location.page}`
                : source.location.cells ||
                  (source.location.line !== undefined
                    ? `Line ${source.location.line}`
                    : "Saved source")}
            </small>
          </summary>
          <div>
            {fields ? (
              <dl>
                {fields.map((field, index) => (
                  <div key={index}>
                    <dt>{field.label}</dt>
                    <dd>{field.value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p>{source.text}</p>
            )}
            {source.truncated && (
              <p className="ol-caption">
                Excerpt shown. Read the full source before recording a decision.
              </p>
            )}
            {sourceUrl && (
              <a href={sourceUrl} target="_blank" rel="noopener noreferrer">
                Open source record <ArrowUpRight size={13} />
              </a>
            )}
            <small className="ol-hash">Source version: {source.hash}</small>
          </div>
        </details>
        <label>
          <input
            type="checkbox"
            checked={selected.includes(id)}
            disabled={busy || changed}
            onChange={(event) =>
              setSelected(
                event.target.checked
                  ? [...selected, id]
                  : selected.filter((entry) => entry !== id),
              )
            }
          />
          I reviewed this evidence
        </label>
      </div>
    );
  }
  return (
    <section className="ol-decision" aria-label={gateLabels[gate]}>
      <header>
        <h4>{gateLabels[gate]}</h4>
        <button type="button" onClick={onClose} aria-label="Close decision">
          <X size={17} />
        </button>
      </header>
      <p>
        This decision applies to these exact files. Completing all required
        approvals lets Rivet place an unsent reply in the linked thread.
      </p>
      {changed && (
        <p className="ol-error">
          The order changed. Close and reopen this decision to review its latest
          state.
        </p>
      )}
      {gate === "engineering" &&
        draftBundles(item).map((bundle) => (
          <div className="ol-drawing-changes" key={bundle.bundle_id}>
            <strong>
              Draft drawing · {bundle.source_revision} to{" "}
              {bundle.target_revision}
            </strong>
            <dl>
              {bundle.changes.map((change) => (
                <div key={change.id}>
                  <dt>{change.target || change.type}</dt>
                  <dd>
                    <span>{change.before}</span>
                    <strong>{change.after}</strong>
                  </dd>
                </div>
              ))}
            </dl>
            {bundle.layout_tasks.map((task) => (
              <p key={task.id}>
                {task.instruction || task.text}{" "}
                <small>Located task; geometry is unchanged.</small>
              </p>
            ))}
          </div>
        ))}
      <fieldset>
        <legend>Evidence for this decision</legend>
        {first.map(evidence)}
        {rest.length > 0 && (
          <details className="ol-more">
            <summary>More package evidence · {rest.length}</summary>
            {rest.map(evidence)}
          </details>
        )}
        {!sources.length && (
          <p className="ol-error">
            Current source evidence is unavailable. Approval is disabled.
          </p>
        )}
      </fieldset>
      <label className="ol-reason">
        Reason for this decision
        <textarea
          rows={3}
          maxLength={4000}
          value={reason}
          disabled={busy || changed}
          onChange={(event) => setReason(event.target.value)}
          placeholder="What did you check or what needs changing?"
        />
      </label>
      {error && (
        <p className="ol-error" role="alert">
          {error}
        </p>
      )}
      <footer>
        <button
          type="button"
          disabled={
            busy || changed || !selected.length || reason.trim().length < 8
          }
          onClick={() => void decide("approve")}
        >
          {busy
            ? "Saving decision…"
            : `Approve ${gate === "money" ? "price" : gate === "scope" ? "scope" : gate === "engineering" ? "engineering review" : "response"}`}
        </button>
        <button
          type="button"
          disabled={
            busy || changed || !selected.length || reason.trim().length < 8
          }
          onClick={() => void decide("reject")}
        >
          Reject package
        </button>
      </footer>
    </section>
  );
}
