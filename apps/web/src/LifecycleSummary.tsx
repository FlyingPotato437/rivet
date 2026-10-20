import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  DownloadSimple,
  FileText,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api, apiFetch, apiUrl, when } from "./api";
import { Modal, RivetIcon } from "./ui";
import { OutlookDraftButton } from "./OutlookDraftButton";
import { ActionExecutionSummary } from "./ActionExecutionSummary";
import type { RecordView } from "./record-types";
import {
  currentLifecyclePackages,
  primaryLifecyclePackage,
  draftBundles,
  gateLabels,
  lifecycleArtifactPath,
  lifecycleActorLabel,
  lifecycleCommercialStatus,
  lifecycleDecisionRequest,
  lifecycleEvidenceFields,
  lifecycleGateEvidence,
  lifecycleHandoffStatus,
  lifecycleStatus,
  type DraftBundle,
  type LifecycleArtifact,
  type LifecycleGate,
  type LifecyclePackage,
  type LifecycleView,
} from "./lifecycle-work";
import "./lifecycle-summary.css";

type Props = {
  orderId: string;
  record: RecordView;
  openSource: (id: string) => void;
};
type PackageProps = Props & {
  item: LifecyclePackage;
  version: number;
  onSaved: (view: LifecycleView) => void;
};
const money = (value: string | number | undefined, currency = "USD") =>
  value === undefined
    ? "—"
    : new Intl.NumberFormat("en-US", { style: "currency", currency }).format(
        Number(value),
      );
const operationNames: Record<string, string> = {
  quote: "Quote prepared",
  kickoff: "Purchase order checked",
  revision_review: "Returned drawings processed",
  purchasing: "Delivery impact prepared",
  revision_check: "Revision checked",
  release: "Release package",
  closeout: "Closeout package",
};

export function useOrderLifecycle(orderId: string) {
  return useQuery({
    queryKey: ["lifecycle", orderId],
    queryFn: () => api<LifecycleView>(`/orders/${orderId}/lifecycle`),
    refetchInterval: 12000,
    retry: 1,
  });
}

export function LifecycleSummary(props: Props) {
  const qc = useQueryClient();
  const query = useOrderLifecycle(props.orderId);
  const view = query.data;
  if (!view?.packages.length) return null;
  const packages = currentLifecyclePackages(view.packages);
  const primary = primaryLifecyclePackage(packages);
  const others = packages.filter((p) => p.id !== primary?.id);
  const historical = view.packages.filter((p) =>
    ["rejected", "superseded"].includes(p.status),
  );
  function onSaved(next: LifecycleView) {
    qc.setQueryData(["lifecycle", props.orderId], next);
    void qc.invalidateQueries({ queryKey: ["record", props.orderId] });
    void qc.invalidateQueries({ queryKey: ["records"] });
  }
  return (
    <section
      className="lifecycle-work"
      aria-label="Rivet’s prepared order work"
    >
      <header className="lifecycle-heading">
        <div className="lifecycle-heading-title">
          <RivetIcon name="agent" size={28} />
          <div>
            <span className="lifecycle-eyebrow">Prepared by Rivet</span>
            <h2>Rivet’s work</h2>
          </div>
        </div>
        <span className="lifecycle-mode">Sample order</span>
      </header>
      <p className="lifecycle-intro">
        {primary?.operation === "closeout"
          ? "Test reports, as-built drawings and handover files, gathered from the order."
          : primary?.operation === "release" ||
              primary?.operation === "revision_check"
            ? "The latest revision checks, release evidence and prepared files."
            : "Prepared responses, drawing changes and commercial checks, with their sources."}
      </p>
      {query.isError && (
        <p className="lifecycle-error" role="alert">
          Could not refresh the prepared work. Showing the last saved version.{" "}
          <button type="button" onClick={() => void query.refetch()}>
            Try again
          </button>
        </p>
      )}
      {primary && (
        <PackageResult
          key={primary.id}
          {...props}
          item={primary}
          version={view.version}
          onSaved={onSaved}
        />
      )}
      <ActionExecutionSummary orderId={props.orderId} />
      {others.length > 0 && (
        <div className="lifecycle-supporting">
          <h3>Other prepared work</h3>
          {others.map((item) => (
            <details className="lifecycle-package-disclosure" key={item.id}>
              <summary>
                <span>
                  <strong>
                    {operationNames[item.operation] || item.title}
                  </strong>
                  <span>{item.title}</span>
                </span>
                <span className="lifecycle-status">
                  {lifecycleStatus(item)}
                </span>
              </summary>
              <PackageResult
                {...props}
                item={item}
                version={view.version}
                onSaved={onSaved}
                compact
              />
            </details>
          ))}
        </div>
      )}
      {historical.length > 0 && (
        <details className="lifecycle-history">
          <summary>Earlier work · {historical.length}</summary>
          {historical.map((item) => (
            <p key={item.id}>
              <strong>{item.title}</strong>
              <span>
                {lifecycleStatus(item)} · {when(item.prepared_at)}
              </span>
            </p>
          ))}
        </details>
      )}
    </section>
  );
}

function PackageResult({
  compact,
  ...props
}: PackageProps & { compact?: boolean }) {
  const { item, orderId, openSource } = props;
  const bundles = draftBundles(item);
  const comments = item.payload.comments || [];
  const counts = item.payload.counts;
  const changeOrder = item.payload.change_order;
  const email = item.payload.email_draft;
  const handoff = lifecycleHandoffStatus(item);
  const [decisionGate, setDecisionGate] = useState<LifecycleGate | null>(null);
  return (
    <article className={`lifecycle-result${compact ? " compact" : ""}`}>
      {!compact && (
        <div className="lifecycle-result-heading">
          <div>
            <h3>
              {item.payload.revision && item.operation === "revision_review"
                ? `Revision ${item.payload.revision} · review prepared`
                : operationNames[item.operation] || item.title}
            </h3>
            <p>
              {when(item.prepared_at)} · {lifecycleStatus(item)}
            </p>
          </div>
          <span className="lifecycle-prepared-mark">
            <Check size={15} /> Saved
          </span>
        </div>
      )}
      {counts && (
        <div className="lifecycle-counts" aria-label="Prepared outputs">
          <span>
            <strong>{counts.answer}</strong> answers drafted
          </span>
          <span>
            <strong>{counts.text_edit}</strong> fields edited
          </span>
          <span>
            <strong>{counts.layout_task}</strong>{" "}
            {counts.layout_task === 1 ? "layout item" : "layout items"} for
            drafting
          </span>
        </div>
      )}
      {bundles.map((bundle) => (
        <DrawingResult
          key={bundle.bundle_id}
          orderId={orderId}
          bundle={bundle}
          openSource={openSource}
        />
      ))}
      {item.payload.lines && (
        <div className="lifecycle-commercial">
          <div>
            <span className="lifecycle-eyebrow">Proposed quote</span>
            <strong className="lifecycle-total">
              {money(item.payload.total, item.payload.currency)}
            </strong>
          </div>
          <ul>
            {item.payload.lines.map((line) => (
              <li key={line.tag}>
                <span>
                  {line.tag} · {line.description}
                  <small>
                    {line.quantity} ×{" "}
                    {money(line.unit_price, item.payload.currency)}
                  </small>
                </span>
                <strong>
                  {money(line.extended_price, item.payload.currency)}
                </strong>
              </li>
            ))}
          </ul>
          {item.payload.exceptions?.map((exception, i) => (
            <p className="lifecycle-note" key={i}>
              {exception.text}
            </p>
          ))}
        </div>
      )}
      {changeOrder && changeOrder.lines.length > 0 && (
        <div className="lifecycle-change-order">
          <div>
            <span className="lifecycle-eyebrow">Change order draft</span>
            <h4>
              {money(changeOrder.total, changeOrder.lines[0]?.currency)}{" "}
              proposed addition
            </h4>
            <p>
              {changeOrder.lines
                .map((line) => `${line.quantity} × ${line.description}`)
                .join("; ")}
            </p>
          </div>
          <span>{lifecycleCommercialStatus(item)}</span>
        </div>
      )}
      {item.payload.part_number && (
        <div className="lifecycle-delivery">
          <span className="lifecycle-eyebrow">
            {item.payload.part_number} · {item.payload.lead_time_days} day
            material lead time
          </span>
          <div>
            <span>{item.payload.current_ship_date}</span>
            <ArrowRight size={18} />
            <strong>{item.payload.proposed_ship_date}</strong>
            <small>Proposed date</small>
          </div>
          <p>{item.payload.basis}</p>
        </div>
      )}
      {item.payload.purchase_order_number && (
        <p className="lifecycle-note">
          Purchase order {item.payload.purchase_order_number} compared with the
          prepared quote.
        </p>
      )}
      {item.payload.total_comments !== undefined && (
        <p className="lifecycle-note">
          {item.payload.documented_resolutions || 0} of{" "}
          {item.payload.total_comments} comments have documented resolutions.{" "}
          {item.payload.bom_matches
            ? "The recorded BOM rows match."
            : "The BOM requires review."}{" "}
          {item.status === "approved"
            ? "Engineering review recorded."
            : "Engineering sign-off is still required."}
        </p>
      )}
      {item.payload.handover && (
        <p className="lifecycle-note">{item.payload.handover}</p>
      )}
      {item.artifacts.some(
        (artifact) =>
          artifact.media_type === "application/pdf" ||
          artifact.name === "handover-documents.zip",
      ) && (
        <div className="lifecycle-document-outputs">
          <span className="lifecycle-eyebrow">
            {item.operation === "closeout"
              ? "Handover documents · verified saved files"
              : "Prepared PDF documents · review before sending"}
          </span>
          <div className="lifecycle-downloads">
            {item.artifacts
              .filter(
                (artifact) =>
                  artifact.media_type === "application/pdf" ||
                  artifact.name === "handover-documents.zip",
              )
              .map((artifact) => (
                <ArtifactLink
                  key={artifact.name}
                  orderId={orderId}
                  ownerId={item.id}
                  artifact={artifact}
                  kind="artifacts"
                  label={
                    (
                      {
                        "quote.pdf": "Quote and exceptions",
                        "change-order.pdf": "Change order",
                        "response-letter.pdf": "Response letter",
                        "drafting-list.pdf": "Changes for drafting",
                        "handover-index.pdf": "Handover index",
                        "handover-documents.zip": "Complete handover bundle",
                      } as Record<string, string>
                    )[artifact.name] || artifact.name
                  }
                />
              ))}
          </div>
        </div>
      )}
      {email && (
        <div
          className={`lifecycle-outlook-handoff ${handoff.tone}`}
          role="status"
        >
          <div>
            <span className="lifecycle-eyebrow">Customer reply</span>
            <h4>{handoff.title}</h4>
            <p>{handoff.detail}</p>
          </div>
          {handoff.canOpen && (
            <OutlookDraftButton
              native
              messageId={item.outlook_handoff?.internet_message_id}
              label={
                item.outlook_handoff?.status === "existing_draft"
                  ? "Open your draft"
                  : "Open reply in Outlook"
              }
            />
          )}
        </div>
      )}
      {email && (
        <details className="lifecycle-output-disclosure">
          <summary>
            <FileText size={18} />
            <span>
              Saved response wording<small>Rivet’s draft</small>
            </span>
          </summary>
          <div className="lifecycle-email">
            <strong>{email.subject}</strong>
            <p>{email.body}</p>
          </div>
        </details>
      )}
      {comments.length > 0 && (
        <details className="lifecycle-output-disclosure">
          <summary>
            <RivetIcon name="comment" size={18} />
            <span>
              Comment responses
              <small>
                {comments.length} comments processed with their sources
              </small>
            </span>
          </summary>
          <div className="lifecycle-comments">
            {comments.map((comment, index) => (
              <article key={comment.comment_id}>
                <div>
                  <span className="lifecycle-comment-number">{index + 1}</span>
                  <div>
                    <h4>{comment.text}</h4>
                    <p>
                      {comment.response ||
                        (comment.kind === "text_edit"
                          ? "The requested field was changed in the draft copy shown above."
                          : "A located drafting task was prepared. Geometry is unchanged.")}
                    </p>
                    <small>
                      {comment.kind === "answer"
                        ? "Reply prepared"
                        : comment.kind === "text_edit"
                          ? "Draft edit prepared"
                          : "Requires a drafter"}
                    </small>
                  </div>
                </div>
                <div className="lifecycle-source-links">
                  {comment.source_ids.map((id, i) => (
                    <button
                      type="button"
                      key={id}
                      onClick={() => openSource(id)}
                    >
                      Source {i + 1}
                    </button>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </details>
      )}
      {item.blockers.length > 0 && (
        <div className="lifecycle-blockers">
          <WarningCircle size={19} />
          <div>
            <h4>What needs resolving</h4>
            <ul>
              {item.blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
      {!item.evidence_current && (
        <p className="lifecycle-error" role="alert">
          The source records changed. This package must be prepared again before
          it can be approved.
        </p>
      )}
      {item.status === "awaiting_decision" &&
        item.evidence_current &&
        !item.blockers.length && (
          <div className="lifecycle-decisions">
            <div>
              <h4>Decisions for this package</h4>
              <p>
                Review only the part you are authorized to approve. Each
                decision is saved against these exact files.
              </p>
            </div>
            <div className="lifecycle-gates">
              {item.pending_gates.map((gate) => (
                <button
                  type="button"
                  key={gate}
                  aria-expanded={decisionGate === gate}
                  onClick={() =>
                    setDecisionGate(decisionGate === gate ? null : gate)
                  }
                >
                  {gateLabels[gate]}
                </button>
              ))}
            </div>
            {decisionGate && (
              <DecisionForm
                key={`${item.id}:${decisionGate}`}
                {...props}
                gate={decisionGate}
                onClose={() => setDecisionGate(null)}
              />
            )}
          </div>
        )}
      {item.decisions.length > 0 && (
        <div className="lifecycle-recorded-decisions">
          {item.decisions.map((decision) => (
            <p key={decision.gate}>
              <Check size={15} />
              <span>
                <strong>
                  {gateLabels[decision.gate]} ·{" "}
                  {decision.decision === "approve" ? "approved" : "rejected"}
                </strong>
                <small>
                  <span title={decision.actor}>
                    {lifecycleActorLabel(decision.actor)}
                  </span>{" "}
                  · {when(decision.at)} · {decision.reason}
                </small>
              </span>
            </p>
          ))}
        </div>
      )}
      <details className="lifecycle-evidence-details">
        <summary>Output files and evidence</summary>
        <div className="lifecycle-downloads">
          {item.artifacts
            .filter(
              (artifact) =>
                artifact.media_type !== "application/pdf" &&
                artifact.name !== "handover-documents.zip",
            )
            .map((artifact) => (
              <ArtifactLink
                key={artifact.name}
                orderId={orderId}
                ownerId={item.id}
                artifact={artifact}
                kind="artifacts"
              />
            ))}
        </div>
        <p>
          Package fingerprint <code>{item.fingerprint}</code>
        </p>
        {Object.entries(item.sources).map(([id, source]) => (
          <div className="lifecycle-evidence-row" key={id}>
            <button type="button" onClick={() => openSource(id)}>
              {props.record.documents.find(
                (doc) => doc.id === source.document_id,
              )?.name || "Open source evidence"}
            </button>
            <code>{source.hash}</code>
          </div>
        ))}
        {item.receipts.map((receipt, i) => (
          <p key={i}>
            {receipt.system} · {receipt.action.replaceAll("_", " ")} · Local
            test adapter{receipt.notice ? ` — ${receipt.notice}` : ""}
          </p>
        ))}
      </details>
    </article>
  );
}

function ArtifactLink({
  orderId,
  ownerId,
  artifact,
  kind,
  label,
}: {
  orderId: string;
  ownerId: string;
  artifact: LifecycleArtifact;
  kind: "drafting" | "artifacts";
  label?: string;
}) {
  const path = lifecycleArtifactPath(orderId, kind, ownerId, artifact);
  if (!path)
    return (
      <span className="lifecycle-error">{artifact.name} is unavailable.</span>
    );
  return (
    <a href={apiUrl(path)} download={artifact.name}>
      <DownloadSimple size={16} />
      <span>{label || artifact.name}</span>
    </a>
  );
}

function DrawingPreview({
  orderId,
  bundle,
  role,
  label,
}: {
  orderId: string;
  bundle: DraftBundle;
  role: string;
  label: string;
}) {
  const artifact =
    bundle.artifacts.find(
      (file) => file.role === role && file.media_type === "image/png",
    ) ||
    bundle.artifacts.find(
      (file) =>
        file.role === role &&
        ["image/svg+xml", "image/png"].includes(file.media_type),
    );
  const path =
    artifact &&
    lifecycleArtifactPath(orderId, "drafting", bundle.bundle_id, artifact);
  const [preview, setPreview] = useState<{ url: string; key: string } | null>(
    null,
  );
  const [enlarged, setEnlarged] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const hash = artifact?.sha256;
  const media = artifact?.media_type;
  const key = `${path}:${hash}`;
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    const controller = new AbortController();
    setError("");
    if (!path || !hash || !media) return;
    void (async () => {
      try {
        const response = await apiFetch(path, { signal: controller.signal });
        if (!response.ok) throw new Error("Could not load this saved drawing.");
        const bytes = await response.arrayBuffer();
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        const actual = [...new Uint8Array(digest)]
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("");
        if (actual !== hash.toLowerCase())
          throw new Error(
            "This preview differs from the saved drawing. Refresh the prepared work.",
          );
        if (!active) return;
        objectUrl = URL.createObjectURL(new Blob([bytes], { type: media }));
        setPreview({ url: objectUrl, key });
      } catch (e) {
        if (active && !controller.signal.aborted)
          setError((e as Error).message);
      }
    })();
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path, hash, media, key, retry]);
  return (
    <figure className="lifecycle-drawing-preview">
      <figcaption>
        <strong>{label}</strong>
        <span>
          {role === "before_preview"
            ? `Source · Rev ${bundle.source_revision}`
            : `Draft copy · Rev ${bundle.target_revision}`}
        </span>
      </figcaption>
      <div className="lifecycle-drawing-canvas">
        {preview?.key === key ? (
          <button
            className="lifecycle-expand-drawing"
            type="button"
            aria-label={`Enlarge ${label.toLowerCase()} drawing`}
            onClick={() => setEnlarged(true)}
          >
            <img
              src={preview.url}
              alt={`${label}: actual saved drawing with equipment labels and revision information`}
            />
            <span>View full size</span>
          </button>
        ) : (
          <p role="status">
            {error ||
              (path
                ? "Loading saved drawing…"
                : "Drawing preview is unavailable.")}
            {error && (
              <button type="button" onClick={() => setRetry((n) => n + 1)}>
                Try again
              </button>
            )}
          </p>
        )}
      </div>
      {artifact && (
        <ArtifactLink
          orderId={orderId}
          ownerId={bundle.bundle_id}
          kind="drafting"
          artifact={artifact}
          label="Download full-size drawing preview"
        />
      )}
      {enlarged && preview?.key === key && (
        <Modal
          className="lifecycle-preview-modal"
          title={`${label} · Revision ${role === "before_preview" ? bundle.source_revision : bundle.target_revision}`}
          eyebrow={
            role === "before_preview"
              ? "Original drawing"
              : "Draft copy · engineering approval required"
          }
          onClose={() => setEnlarged(false)}
          wide
        >
          <div className="lifecycle-full-drawing">
            <img src={preview.url} alt={`${label} drawing, full size`} />
          </div>
        </Modal>
      )}
    </figure>
  );
}

function DrawingResult({
  orderId,
  bundle,
  openSource,
}: {
  orderId: string;
  bundle: DraftBundle;
  openSource: (id: string) => void;
}) {
  return (
    <section className="lifecycle-drawing-result">
      <div className="lifecycle-drawing-heading">
        <div>
          <span className="lifecycle-eyebrow">
            Actual drawing and BOM files
          </span>
          <h4>
            Revision {bundle.source_revision} <span>to</span>{" "}
            {bundle.target_revision}
          </h4>
        </div>
        <span className="lifecycle-draft-label">
          Draft · engineering approval required
        </span>
      </div>
      <div className="lifecycle-previews">
        <DrawingPreview
          orderId={orderId}
          bundle={bundle}
          role="before_preview"
          label="Before"
        />
        <DrawingPreview
          orderId={orderId}
          bundle={bundle}
          role="after_preview"
          label="Rivet’s draft"
        />
      </div>
      <div
        className="lifecycle-change-table"
        role="table"
        aria-label="Fields changed in the draft"
      >
        <div className="lifecycle-change-table-head" role="row">
          <span role="columnheader">Changed field</span>
          <span role="columnheader">Before</span>
          <span role="columnheader">Draft value</span>
        </div>
        {bundle.changes.map((change) => (
          <div role="row" key={change.id}>
            <span role="cell">
              {change.target || change.type.replaceAll("_", " ")}
              {change.evidence_id && (
                <button
                  type="button"
                  onClick={() =>
                    change.evidence_id && openSource(change.evidence_id)
                  }
                >
                  View evidence
                </button>
              )}
            </span>
            <span role="cell" className="lifecycle-before-value">
              {change.before || "Empty"}
            </span>
            <strong role="cell">{change.after}</strong>
          </div>
        ))}
      </div>
      {bundle.layout_tasks.length > 0 && (
        <div className="lifecycle-layout-note">
          <RivetIcon name="drawing" size={21} />
          <div>
            <strong>
              {bundle.layout_tasks.length}{" "}
              {bundle.layout_tasks.length === 1
                ? "positioned task"
                : "positioned tasks"}{" "}
              prepared for a drafter
            </strong>
            <p>
              Locations are marked in the draft preview. Geometry has not been
              moved; layout and clearance checks remain with engineering.
            </p>
          </div>
        </div>
      )}
      <div className="lifecycle-downloads">
        {bundle.artifacts
          .filter((a) => ["draft_drawing", "draft_bom"].includes(a.role || ""))
          .map((artifact) => (
            <ArtifactLink
              key={artifact.name}
              orderId={orderId}
              ownerId={bundle.bundle_id}
              kind="drafting"
              artifact={artifact}
              label={
                artifact.role === "draft_drawing"
                  ? "Download draft DXF"
                  : "Download revised BOM"
              }
            />
          ))}
      </div>
    </section>
  );
}

function DecisionForm({
  item,
  record,
  version,
  orderId,
  openSource,
  onSaved,
  gate,
  onClose,
}: PackageProps & { gate: LifecycleGate; onClose: () => void }) {
  const [fingerprint] = useState(item.fingerprint);
  const [reason, setReason] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const evidence = record.sources.filter((source) => item.sources[source.id]);
  const available = evidence.map((source) => source.id);
  const groupedEvidence = lifecycleGateEvidence(item, gate, available);
  const relevant = groupedEvidence.relevant.slice(0, 4);
  const remainingRelevant = groupedEvidence.relevant.slice(4);
  const evidenceById = new Map(evidence.map((source) => [source.id, source]));
  const evidenceLabels: Record<LifecycleGate, string> = {
    money: "Price calculation and billable scope",
    scope: "Customer scope and contract basis",
    engineering: "Draft edits and engineering constraints",
    customer_commitment: "Prepared answers and customer wording",
  };
  const canDecide =
    !busy &&
    selected.length > 0 &&
    reason.trim().length >= 8 &&
    item.evidence_current &&
    fingerprint === item.fingerprint;
  async function decide(decision: "approve" | "reject") {
    setError("");
    try {
      const body = lifecycleDecisionRequest(
        item,
        version,
        fingerprint,
        gate,
        decision,
        reason,
        selected,
        available,
      );
      setBusy(true);
      const next = await api<LifecycleView>(
        `/orders/${orderId}/lifecycle/decisions/${item.id}`,
        body,
      );
      onSaved(next);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function evidenceRow(id: string) {
    const source = evidenceById.get(id);
    if (!source) return null;
    const fields = lifecycleEvidenceFields(source.text);
    const location = [
      source.location.page !== undefined ? `Page ${source.location.page}` : "",
      source.location.line !== undefined ? `Line ${source.location.line}` : "",
      source.location.cells || "",
    ]
      .filter(Boolean)
      .join(" · ");
    return (
      <div className="lifecycle-decision-evidence" key={source.id}>
        <label>
          <input
            type="checkbox"
            checked={selected.includes(source.id)}
            onChange={(event) =>
              setSelected(
                event.target.checked
                  ? [...selected, source.id]
                  : selected.filter((value) => value !== source.id),
              )
            }
          />
          <span>
            <strong>
              {record.documents.find((doc) => doc.id === source.document_id)
                ?.name || "Source record"}
            </strong>
            {location && (
              <small className="lifecycle-evidence-location">{location}</small>
            )}
            {fields ? (
              <span className="lifecycle-fact-list">
                {fields.map((field, index) => (
                  <span
                    className="lifecycle-fact"
                    key={`${index}:${field.label}`}
                  >
                    <span>{field.label}</span>
                    <b>{field.value}</b>
                  </span>
                ))}
              </span>
            ) : (
              <span>{source.text}</span>
            )}
          </span>
        </label>
        <button type="button" onClick={() => openSource(source.id)}>
          Open source
        </button>
      </div>
    );
  }
  return (
    <section className="lifecycle-decision-form" aria-label={gateLabels[gate]}>
      <header>
        <h4>{gateLabels[gate]}</h4>
        <button type="button" onClick={onClose} aria-label="Close decision">
          <X size={18} />
        </button>
      </header>
      <p>
        This records your decision on this package only.
        {["quote", "revision_review"].includes(item.operation)
          ? " When all required approvals are complete, Rivet can place an unsent draft in the linked Outlook thread. You send it from Outlook."
          : " It does not send a customer response or establish a production release."}
      </p>
      <fieldset>
        <legend>{evidenceLabels[gate]}</legend>
        <p className="lifecycle-evidence-instruction">
          Select only the evidence you have reviewed.
          {selected.length > 0
            ? ` ${selected.length} selected.`
            : " Nothing is selected automatically."}
        </p>
        {relevant.map(evidenceRow)}
        {remainingRelevant.length > 0 && (
          <details className="lifecycle-more-evidence">
            <summary>
              More evidence for this decision · {remainingRelevant.length}
            </summary>
            {remainingRelevant.map(evidenceRow)}
          </details>
        )}
        {groupedEvidence.additional.length > 0 && (
          <details
            className="lifecycle-more-evidence"
            open={!groupedEvidence.relevant.length || undefined}
          >
            <summary>
              Other package evidence · {groupedEvidence.additional.length}
            </summary>
            {groupedEvidence.additional.map(evidenceRow)}
          </details>
        )}
        {!evidence.length && (
          <p>
            The supporting evidence is unavailable in this record. A decision
            cannot be recorded yet.
          </p>
        )}
      </fieldset>
      <label className="lifecycle-decision-reason">
        Reason for this decision
        <textarea
          rows={3}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="What did you verify, or what needs to change?"
          maxLength={4000}
        />
      </label>
      {error && (
        <p className="lifecycle-error" role="alert">
          {error}
        </p>
      )}
      <footer>
        <button
          type="button"
          disabled={!canDecide}
          onClick={() => void decide("reject")}
        >
          Reject package
        </button>
        <button
          className="lifecycle-approve"
          type="button"
          disabled={!canDecide}
          onClick={() => void decide("approve")}
        >
          <Check size={16} />
          {busy
            ? "Saving decision…"
            : `Approve ${gate === "money" ? "price" : gate === "customer_commitment" ? "response" : gate === "scope" ? "scope" : "engineering review"}`}
        </button>
      </footer>
    </section>
  );
}
