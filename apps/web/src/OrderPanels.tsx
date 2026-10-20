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
        <span className="revision-chip">
          <StatusIcon kind="done" size={13} />
          <span className="mono">{counts.verified}</span> made
        </span>
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
      {(hidden > 0 || all) && (
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
