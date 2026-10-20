import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight } from "@phosphor-icons/react";
import { api, when } from "./api";
import type { ProjectFlow } from "./project-flow";
import {
  equipmentChecks,
  equipmentCounts,
  equipmentStatus,
  type EquipmentCheck,
  type EquipmentEvidence,
  type EquipmentView,
} from "./equipment-types";
import {
  correctedBundleReady,
  correctedChecks,
  correctedFiles,
  newestCorrectedBundle,
  type CorrectedBundle,
} from "./equipment-correction-types";
import { useEquipmentCorrections } from "./EquipmentCorrections";
import { decisionLabel, useDecision } from "./WorkDetails";
import { pmSentence, type DecisionItem } from "./work-presentation";
import {
  CustomerChip,
  Monogram,
  StatusIcon,
  statusKind,
  type StatusKind,
} from "./StatusIcon";

/** Shares the cache entry EquipmentEntry and EquipmentReview already poll. */
export function useEquipmentView(id: string) {
  return useQuery({
    queryKey: ["equipment", id],
    queryFn: () => api<EquipmentView>(`/orders/${id}/equipment`),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
}

/** The name, with the last " · " segment in the secondary headline tone. */
export function TwoToneTitle({ name }: { name: string }) {
  const at = name.lastIndexOf(" · ");
  return at > 0 ? (
    <>
      {name.slice(0, at)}
      <span className="title-tone">{name.slice(at)}</span>
    </>
  ) : (
    <>{name}</>
  );
}

export function TitleBlock({
  order,
  revision,
}: {
  order: ProjectFlow;
  revision?: string;
}) {
  const equipment = useEquipmentView(order.id).data?.comparison;
  const from = equipment?.baseline_revision,
    to = revision || equipment?.target_revision;
  return (
    <div className="title-block" aria-label="Order details">
      <div>
        <span>Job</span>
        <strong className="mono">{order.number}</strong>
      </div>
      <div>
        <span>Customer</span>
        <strong>
          {order.customer ? (
            <>
              <Monogram name={order.customer} />
              <span className="title-block-ellipsis">{order.customer}</span>
            </>
          ) : (
            "Not linked"
          )}
        </strong>
      </div>
      <div>
        <span>Revision</span>
        <strong className="mono">
          {from && to && from !== to ? (
            <>
              {from} → <em>{to}</em>
            </>
          ) : (
            to || "—"
          )}
        </strong>
      </div>
      <div>
        <span>Files</span>
        <strong className="mono">
          {order.document_count} · {order.email_count}{" "}
          {order.email_count === 1 ? "email" : "emails"}
        </strong>
      </div>
      <div>
        <span>Updated</span>
        <strong className="mono">{when(order.updated_at)}</strong>
      </div>
    </div>
  );
}

const checkText = {
  verified: "Made",
  remaining: "Not made",
  cannot_verify: "Can’t verify",
  unchanged: "Unchanged",
};
const checkKind: Record<keyof typeof checkText, StatusKind> = {
  verified: "done",
  remaining: "missing",
  cannot_verify: "waiting",
  unchanged: "waiting",
};

function where(e?: EquipmentEvidence) {
  if (!e) return "";
  const parts = [
    e.role === "bom" || /\.(xlsx|csv)$/i.test(e.name) ? "BOM" : "Drawing",
  ];
  if (e.revision) parts[0] += ` ${e.revision}`;
  if (e.sheet) parts.push(`Sheet ${e.sheet}`);
  if (e.page) parts.push(`p. ${e.page}`);
  if (e.row) parts.push(`Row ${e.row}`);
  if (e.cell) parts.push(e.cell);
  return parts.join(" · ");
}

function value(check: EquipmentCheck) {
  const v =
    check.required?.value ??
    check.target_drawing?.value ??
    check.target_bom?.value;
  return v === null || v === undefined || v === "" ? "" : String(v);
}

function fixedIn(check: EquipmentCheck, bundle?: CorrectedBundle) {
  if (!bundle || bundle.current === false) return "";
  return correctedChecks(bundle).some(
    (c) =>
      c.device_tag === check.device_tag &&
      c.field === check.field &&
      equipmentStatus(c.status, bundle.verification.stale) === "verified",
  )
    ? bundle.target_revision
    : "";
}

/** Each requested change, whether the revised drawing and BOM actually made it, and where. */
export function RevisionCheck({
  order,
  openReview,
}: {
  order: ProjectFlow;
  openReview: () => void;
}) {
  const q = useEquipmentView(order.id),
    corrections = useEquipmentCorrections(order.id);
  const [all, setAll] = useState(false);
  const comparison = q.data?.comparison;
  if (!comparison?.checks.length) return null;
  const bundle = newestCorrectedBundle(corrections.data);
  const counts = equipmentCounts(comparison);
  const checks = equipmentChecks(comparison);
  const changed = checks.filter(
    (c) => equipmentStatus(c.status, comparison.stale) !== "unchanged",
  );
  const rows = all ? checks : changed.slice(0, 6);
  const hidden = checks.length - rows.length;
  return (
    <section className="revision-check" aria-label="Revision check">
      <header>
        <strong>Revision check</strong>
        <span className="mono">
          Rev {comparison.baseline_revision} → Rev {comparison.target_revision}
        </span>
        <span className="revision-check-space" />
        {counts.verified > 0 && (
          <span className="revision-chip">
            <StatusIcon kind="done" size={13} />
            <span className="mono">{counts.verified}</span> made
          </span>
        )}
        {!counts.verified && !counts.remaining && counts.unchanged > 0 && (
          <span className="revision-chip">
            <StatusIcon kind="waiting" size={13} />
            <span className="mono">{counts.unchanged}</span> unchanged
          </span>
        )}
        {counts.remaining > 0 && (
          <span className="revision-chip">
            <StatusIcon kind="missing" size={13} />
            <span className="mono">{counts.remaining}</span> not made
          </span>
        )}
        {counts.cannot_verify > 0 && (
          <span className="revision-chip">
            <StatusIcon kind="waiting" size={13} />
            <span className="mono">{counts.cannot_verify}</span> can’t verify
          </span>
        )}
        <button className="text-button" onClick={openReview}>
          Open review <ArrowUpRight size={13} />
        </button>
      </header>
      {comparison.stale && (
        <p className="revision-check-note">
          The documents changed since this check. Open the review to re-check
          the latest revision.
        </p>
      )}
      {!rows.length && (
        <p className="revision-check-note">
          No field changed between Rev {comparison.baseline_revision} and Rev{" "}
          {comparison.target_revision}. {counts.unchanged} checked fields are
          unchanged.
        </p>
      )}
      <ol>
        {rows.map((check) => {
          const status = equipmentStatus(check.status, comparison.stale);
          const fixed = status === "remaining" ? fixedIn(check, bundle) : "";
          const evidence =
            check.target_drawing?.evidence?.[0] ||
            check.target_bom?.evidence?.[0];
          const label = check.label.startsWith(`${check.device_tag} · `)
            ? check.label.slice(check.device_tag.length + 3)
            : check.label;
          return (
            <li
              key={check.id}
              className={`is-${status}${fixed ? " is-fixed" : ""}`}
            >
              <button onClick={openReview} title={check.reason}>
                <StatusIcon kind={fixed ? "done" : checkKind[status]} />
                <span className="mono revision-tag">{check.device_tag}</span>
                <span className="revision-text">
                  {label}
                  {value(check) && (
                    <span className="mono"> · {value(check)}</span>
                  )}
                </span>
                <span className="revision-state">
                  {checkText[status]}
                  {fixed && <em> · fixed in {fixed}</em>}
                </span>
                <span className="mono revision-where">{where(evidence)}</span>
              </button>
            </li>
          );
        })}
      </ol>
      {rows.length > 0 && (hidden > 0 || all) && (
        <button className="revision-more" onClick={() => setAll(!all)}>
          {all
            ? "Show changes only"
            : hidden === counts.unchanged
              ? `Show ${hidden} unchanged`
              : `Show ${hidden} more`}
        </button>
      )}
    </section>
  );
}

const extension = (name: string) =>
  (name.split(".").pop() || "").slice(0, 4).toUpperCase();

/** Corrected files Rivet saved, each with the result of re-checking it. */
export function CorrectedFiles({ order }: { order: ProjectFlow }) {
  const corrections = useEquipmentCorrections(order.id);
  const bundle = newestCorrectedBundle(corrections.data);
  if (!bundle) return null;
  const files = correctedFiles(bundle);
  if (!files.length) return null;
  const summary = bundle.verification?.summary;
  const open = summary
    ? summary.remaining + summary.cannot_verify + (summary.source_issues || 0)
    : 0;
  const checked = correctedBundleReady(bundle)
    ? "Re-checked · 0 issues"
    : summary
      ? `Re-checked · ${open} open`
      : "Saved";
  return (
    <div className="corrected-files">
      {files.map((file) => (
        <a
          key={file.sha256 || file.name}
          href={file.url}
          target="_blank"
          rel="noreferrer"
        >
          <span className="file-tile">{extension(file.name)}</span>
          <span>
            <span className="mono">{file.name}</span>
            <small>{checked}</small>
          </span>
        </a>
      ))}
    </div>
  );
}

export function OrderStatusLine({ order }: { order: ProjectFlow }) {
  return (
    <span className="order-status-line">
      <StatusIcon kind={statusKind(order.status)} />
      {order.status_label}
    </span>
  );
}

export { CustomerChip };

function DecisionLine({
  item,
  seeWhy,
}: {
  item: DecisionItem;
  seeWhy: () => void;
}) {
  const { busy, error, result, decide } = useDecision(item);
  return (
    <li className={result ? "is-decided" : ""}>
      <StatusIcon kind={result ? "done" : "need"} />
      <button className="needs-you-item" onClick={seeWhy}>
        <strong>{item.title}</strong>
        <span>{error || item.reason}</span>
      </button>
      {result ? (
        <span className="needs-you-result" role="status">
          {result}
        </span>
      ) : (
        <span className="needs-you-actions">
          <button className="text-button" onClick={seeWhy}>
            See why
          </button>
          <button
            className="primary"
            disabled={busy}
            onClick={() => void decide("approve")}
          >
            {busy ? "Saving…" : decisionLabel(item)}
          </button>
        </span>
      )}
    </li>
  );
}

/** The one thing Rivet is waiting on, stated plainly at the top of the order. */
export function NeedsYouPanel({
  order,
  decisions,
  seeWhy,
  openReview,
  openComments,
}: {
  order: ProjectFlow;
  decisions: DecisionItem[];
  seeWhy: (item: DecisionItem) => void;
  openReview: () => void;
  openComments: () => void;
}) {
  if (!decisions.length && order.status !== "needs_you") return null;
  const headline = decisions.length
    ? `${decisions.length} ${decisions.length === 1 ? "decision needs" : "decisions need"} your approval`
    : pmSentence(order.explanation, "Rivet needs your input to continue.");
  return (
    <section className="needs-you-panel" aria-label="Needs your decision">
      <span className="needs-you-cap">
        <StatusIcon kind="need" size={13} />
        Needs your decision
      </span>
      <h2>{headline}</h2>
      {decisions.length ? (
        <ol className="needs-you-list">
          {decisions.map((item) => (
            <DecisionLine
              key={item.id}
              item={item}
              seeWhy={() => seeWhy(item)}
            />
          ))}
        </ol>
      ) : (
        <>
          {!!order.open_points.length && (
            <ul className="needs-you-points">
              {order.open_points.map((point, i) => (
                <li key={i}>{point}</li>
              ))}
            </ul>
          )}
          <div className="needs-you-footer">
            <button className="primary" onClick={openReview}>
              Open the drawing and BOM check
            </button>
            <button className="secondary" onClick={openComments}>
              Drawing and comments
            </button>
          </div>
        </>
      )}
    </section>
  );
}
