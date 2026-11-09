import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CircleNotch,
  DownloadSimple,
  FileText,
} from "@phosphor-icons/react";
import { api, apiFetch, when } from "./api";
import { ErrorNote, RivetIcon } from "./ui";
import { equipmentValue } from "./equipment-types";
import { safeOutlookUrl } from "./outlook-workflow";
import { NativeDrawingReview } from "./NativeDrawingReview";
import { nativeChanges, nativeChecksPass } from "./native-drawing-types";
import {
  correctionArtifactPath,
  correctionBefore,
  correctionIssue,
  correctedBundleReady,
  correctedDraftReady,
  correctionDelivery,
  correctedFiles,
  correctedChecks,
  literalCorrectionEdits,
  newestCorrectedBundle,
  type CorrectedArtifact,
  type EquipmentCorrectionsView,
} from "./equipment-correction-types";
import "./equipment-review.css";

export function useEquipmentCorrections(id: string) {
  return useQuery({
    queryKey: ["equipment-corrections", id],
    queryFn: () =>
      api<EquipmentCorrectionsView>(`/orders/${id}/equipment/corrections`),
    staleTime: 15_000,
    refetchInterval: (query) =>
      ["queued", "working"].includes(query.state.data?.automation?.status || "")
        ? 2_500
        : 15_000,
    retry: false,
    enabled: !!id,
  });
}

export function EquipmentCorrections({
  id,
  compact = false,
}: {
  id: string;
  compact?: boolean;
}) {
  const q = useEquipmentCorrections(id),
    qc = useQueryClient();
  const [busy, setBusy] = useState<"prepare" | "outlook" | "">(""),
    [error, setError] = useState("");
  const view = q.data,
    bundle = newestCorrectedBundle(view);
  const automaticWorking = ["queued", "working"].includes(
    view?.automation?.status || "",
  );
  async function act(action: "prepare" | "outlook") {
    if (
      !view ||
      busy ||
      automaticWorking ||
      (action === "outlook" && (!bundle || !correctionDelivery(bundle).canSave))
    )
      return;
    setBusy(action);
    setError("");
    try {
      const next = await api<EquipmentCorrectionsView>(
        `/orders/${id}/equipment/corrections${action === "prepare" ? "/prepare" : `/${bundle!.id}/outlook`}`,
        action === "prepare"
          ? {
              expected_version: view.version,
              expected_plan_id: view.plan.plan_id,
            }
          : { expected_version: view.version },
      );
      qc.setQueryData(["equipment-corrections", id], next);
      await Promise.all(
        [
          ["equipment", id],
          ["order-activity", id],
          ["project-work"],
          ["record", id],
          ["outlook-lifecycle"],
        ].map((queryKey) => qc.invalidateQueries({ queryKey })),
      );
    } catch (e) {
      setError((e as Error).message);
      void q.refetch();
    } finally {
      setBusy("");
    }
  }
  if (q.isPending)
    return (
      <div className="correction-loading" role="status">
        Opening the checked drawing and BOM…
      </div>
    );
  if (!view)
    return (
      <div className="correction-unavailable">
        File preparation could not load.{" "}
        <button onClick={() => void q.refetch()}>Try again</button>
      </div>
    );
  const ready = !!bundle && correctedBundleReady(bundle),
    inOutlook = !!bundle && correctedDraftReady(bundle),
    outlook = bundle?.outlook?.draft;
  const link = safeOutlookUrl(outlook?.web_link || outlook?.outlook_url);
  const delivery = bundle ? correctionDelivery(bundle) : null;
  const blocked = view.plan.status === "blocked";
  if (
    compact &&
    !bundle &&
    !view.plan.edits.length &&
    !nativeChanges(view.plan.native_plan).length
  )
    return null;
  return (
    <section
      className={`equipment-corrections${compact ? " is-compact" : ""}`}
      aria-label="Rivet’s corrected files"
    >
      <header className="correction-heading">
        <span className="correction-symbol">
          <RivetIcon name="revision" size={23} />
        </span>
        <div>
          <span className="section-eyebrow">Rivet’s work</span>
          <h3>
            {busy === "prepare"
              ? "Preparing and re-checking the files"
              : busy === "outlook"
                ? "Saving the corrected reply in Outlook"
                : delivery?.state === "sent"
                  ? "Corrected reply sent from Outlook"
                  : delivery?.state === "closed" || delivery?.state === "review"
                    ? delivery.label
                    : automaticWorking && !inOutlook
                      ? ready
                        ? "Rivet is completing the Outlook draft"
                        : "Rivet is preparing and re-checking the files"
                      : inOutlook
                        ? "Corrected draft ready in Outlook"
                        : ready
                          ? "Corrected files, checked against the requirement"
                          : bundle
                            ? "Draft files prepared. Checks still need attention."
                            : blocked
                              ? "A drafting decision is needed first"
                              : view.plan.status === "ready"
                                ? "Rivet can prepare the corrected files"
                                : "No supported file changes to prepare"}
          </h3>
        </div>
        {(busy ||
          automaticWorking ||
          ["preparing", "queued", "running"].includes(
            bundle?.status || "",
          )) && <CircleNotch size={19} className="project-spinning" />}
      </header>
      {error && <ErrorNote message={error} />}
      {view.automation?.status === "failed" && view.automation.error && (
        <ErrorNote message={view.automation.error} />
      )}
      {!bundle && (
        <>
          {!compact && (
            <NativeDrawingReview
              verification={view.plan.native_plan}
              proposed
            />
          )}
          {!compact && literalCorrectionEdits(view).length > 0 && (
            <div
              className="correction-plan"
              aria-label="Planned equipment changes"
            >
              {literalCorrectionEdits(view).map((edit, i) => (
                <div key={`${edit.device_tag}:${edit.field}:${i}`}>
                  <span>
                    <strong>{edit.device_tag}</strong>
                    <small>
                      {edit.label || edit.field.replaceAll("_", " ")}
                      {edit.role
                        ? ` · ${edit.role === "bom" ? "BOM" : edit.role}`
                        : ""}
                    </small>
                  </span>
                  <span className="correction-before">
                    {correctionBefore(edit)}
                  </span>
                  <ArrowRight size={14} />
                  <span className="correction-after">
                    {equipmentValue(edit.after, edit.field)}
                  </span>
                </div>
              ))}
            </div>
          )}
          {!!view.plan.blockers.length && (
            <div className="correction-blockers">
              <p>
                Rivet will preserve the originals and leave these changes to
                engineering.
              </p>
              <ul>
                {view.plan.blockers.map((issue, i) => (
                  <li key={i}>{correctionIssue(issue)}</li>
                ))}
              </ul>
            </div>
          )}
          {view.plan.status === "ready" && (
            <div className="correction-actions">
              <button
                className="primary"
                disabled={!!busy || automaticWorking}
                onClick={() => void act("prepare")}
              >
                {busy === "prepare" || automaticWorking
                  ? "Preparing files…"
                  : "Prepare corrected draft"}
              </button>
              <span>Draft copies only · originals stay unchanged</span>
            </div>
          )}
        </>
      )}
      {bundle && (
        <>
          <div className="correction-result-line">
            <span className={`work-state${inOutlook ? " is-ready" : ""}`}>
              <span />
              {delivery?.label}
            </span>
            <span>
              {bundle.target_revision
                ? `Revision ${bundle.target_revision} · `
                : ""}
              {when(bundle.created_at)}
            </span>
          </div>
          <div className="correction-actions">
            {delivery?.openLabel && link ? (
              <a
                className="primary"
                href={link}
                target="_blank"
                rel="noreferrer"
              >
                {delivery.openLabel} <ArrowUpRight size={14} />
              </a>
            ) : delivery?.canSave ? (
              <button
                className="primary"
                disabled={!!busy || automaticWorking}
                onClick={() => void act("outlook")}
              >
                {busy === "outlook" || automaticWorking
                  ? "Saving to Outlook…"
                  : "Save corrected reply in Outlook"}
                <ArrowUpRight size={14} />
              </button>
            ) : null}
            <span>{delivery?.note}</span>
          </div>
          {!compact && (
            <>
              <NativeDrawingReview verification={bundle.native_verification} />
              {bundle.company_rules && (
                <section
                  className="correction-company-checks"
                  aria-label="Company rules checked against the saved files"
                >
                  <header>
                    <h4>Company checks</h4>
                    <span>
                      {
                        bundle.company_rules.results.filter(
                          (result) =>
                            result.applicable !== false &&
                            result.status === "pass",
                        ).length
                      }{" "}
                      passed
                    </span>
                  </header>
                  {bundle.company_rules.results
                    .filter((result) => result.applicable !== false)
                    .map((result) => (
                      <article key={result.rule_id}>
                        <span
                          className={`company-check-status is-${result.status}`}
                        >
                          {result.status === "pass" ? (
                            <Check size={15} />
                          ) : (
                            "Check"
                          )}
                        </span>
                        <div>
                          <strong>{result.label}</strong>
                          <p>{result.reason}</p>
                          {result.rule_sources[0] && (
                            <small>
                              {result.rule_sources[0].name}
                              {result.rule_sources[0].location.page
                                ? ` · page ${result.rule_sources[0].location.page}`
                                : ""}
                            </small>
                          )}
                        </div>
                      </article>
                    ))}
                  {!bundle.company_rules.results.some(
                    (result) => result.applicable !== false,
                  ) && <p>No company rule applies to this package.</p>}
                  {bundle.company_rules.blockers.map((issue, index) => (
                    <p key={index} role="status">
                      {issue}
                    </p>
                  ))}
                </section>
              )}
              {!!correctedChecks(bundle).length && (
                <div
                  className="correction-proof"
                  aria-label="Values read from the saved corrected files"
                >
                  <div className="correction-proof-heading">
                    <span>Re-checked equipment values</span>
                    <span>Drawing</span>
                    <span>BOM</span>
                    <span />
                  </div>
                  {correctedChecks(bundle).map((check) => (
                    <div key={check.id || `${check.device_tag}:${check.field}`}>
                      <span>
                        <strong>{check.device_tag}</strong>
                        <small>{check.field.replaceAll("_", " ")}</small>
                      </span>
                      <span>
                        {equipmentValue(
                          check.target_drawing?.value,
                          check.field,
                        )}
                      </span>
                      <span>
                        {equipmentValue(check.target_bom?.value, check.field)}
                      </span>
                      <span>
                        {check.status === "verified" ? (
                          <Check size={15} />
                        ) : (
                          "Check"
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              <div className="correction-file-list">
                {correctedFiles(bundle).map((file) => (
                  <CorrectionFile
                    key={file.name}
                    orderId={id}
                    bundleId={bundle.id}
                    artifact={file}
                  />
                ))}
              </div>
              {bundle.verification?.summary && (
                <p className="correction-verification">
                  {ready && <Check size={14} />}
                  {bundle.native_verification ? (
                    nativeChecksPass(bundle.native_verification) ? (
                      "Saved drawing, connections and BOM independently rechecked"
                    ) : (
                      "Native drawing checks need attention"
                    )
                  ) : (
                    <>
                      {bundle.verification.summary.verified} verified ·{" "}
                      {bundle.verification.summary.remaining} remaining ·{" "}
                      {bundle.verification.summary.cannot_verify} cannot verify
                      {bundle.verification.summary.source_issues
                        ? ` · ${bundle.verification.summary.source_issues} source issues`
                        : ""}
                    </>
                  )}
                </p>
              )}
            </>
          )}
          {compact && inOutlook && (
            <div className="correction-compact-files">
              {outlook?.attachments?.map((file) => (
                <div key={file.name}>
                  <FileText size={14} />
                  <span>{file.name}</span>
                </div>
              ))}
            </div>
          )}
          {(outlook?.error || bundle.outlook?.error) && (
            <p className="correction-issue" role="status">
              {outlook?.error || bundle.outlook?.error}
            </p>
          )}
          {outlook?.status === "existing_draft" && (
            <p className="correction-issue">
              An existing Outlook draft was preserved. Rivet has not replaced
              your wording or its attachments.
            </p>
          )}
        </>
      )}
    </section>
  );
}

export function CorrectionFile({
  orderId,
  bundleId,
  artifact,
}: {
  orderId: string;
  bundleId: string;
  artifact: CorrectedArtifact;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function open() {
    const path = correctionArtifactPath(orderId, bundleId, artifact);
    if (!path) {
      setError("This file reference is unavailable.");
      return;
    }
    setBusy(true);
    setError("");
    const pdf = artifact.media_type === "application/pdf",
      preview = pdf ? window.open("", "_blank") : null;
    if (preview) {
      preview.opener = null;
      preview.document.title = "Rivet · Opening checked drawing";
      preview.document.body.textContent = "Opening your checked drawing…";
      preview.document.body.style.cssText =
        "margin:0;min-height:100vh;display:grid;place-items:center;background:#15191f;color:#dce1e8;font:16px/1.7 system-ui,sans-serif";
    }
    const controller = new AbortController();
    let timer = 0;
    try {
      const blob = await Promise.race([
        (async () => {
          const res = await apiFetch(path, { signal: controller.signal });
          if (!res.ok) {
            const body = await res.json().catch(() => null);
            const detail = body?.detail;
            throw new Error(
              typeof detail === "string"
                ? detail
                : detail?.message || "The corrected file could not be opened.",
            );
          }
          return res.blob();
        })(),
        new Promise<never>((_, reject) => {
          timer = window.setTimeout(() => {
            controller.abort();
            reject(new Error("The drawing took too long to open. Try again."));
          }, 30_000);
        }),
      ]);
      const url = URL.createObjectURL(blob);
      if (preview) preview.location.href = url;
      else if (pdf) {
        URL.revokeObjectURL(url);
        throw new Error("Allow the PDF to open in a new tab, then try again.");
      } else {
        const a = document.createElement("a");
        a.href = url;
        a.download = artifact.name;
        a.click();
      }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      preview?.close();
      setError((e as Error).message);
    } finally {
      window.clearTimeout(timer);
      setBusy(false);
    }
  }
  return (
    <div>
      <button onClick={() => void open()} disabled={busy}>
        <FileText size={17} />
        <span>
          <strong>{artifact.name}</strong>
          <small>
            {artifact.role === "bom"
              ? "Corrected bill of materials"
              : artifact.media_type === "application/pdf"
                ? "Checked drawing PDF"
                : artifact.role === "drawing"
                  ? "Editable drawing copy"
                  : "Supporting file"}
          </small>
        </span>
        {busy ? (
          <CircleNotch className="project-spinning" size={15} />
        ) : artifact.media_type === "application/pdf" ? (
          <ArrowUpRight size={14} />
        ) : (
          <DownloadSimple size={14} />
        )}
      </button>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
