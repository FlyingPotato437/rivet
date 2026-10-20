import { Fragment, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUpRight,
  CaretDown,
  Check,
  FileText,
  UploadSimple,
} from "@phosphor-icons/react";
import { api, apiFetch, when } from "./api";
import { Busy, ErrorNote } from "./ui";
import {
  equipmentCounts,
  equipmentChecks,
  equipmentEvidencePath,
  equipmentLocation,
  equipmentPair,
  equipmentRevisions,
  equipmentRole,
  equipmentStatus,
  equipmentSources,
  equipmentValue,
  equipmentVerdict,
  type EquipmentCell,
  type EquipmentCheck,
  type EquipmentEvidence,
  type EquipmentRole,
  type EquipmentView,
} from "./equipment-types";
import "./equipment-review.css";
import {
  EquipmentCorrections,
  useEquipmentCorrections,
} from "./EquipmentCorrections";
import { OrderManufacturerProfile } from "./OrderManufacturerProfile";
import { DrawingModelEvidence } from "./DrawingModelEvidence";
import {
  correctedBundleReady,
  correctedDraftReady,
  correctedFiles,
  newestCorrectedBundle,
} from "./equipment-correction-types";

const statusLabels = {
  verified: "Verified",
  unchanged: "Unchanged",
  remaining: "Remaining",
  cannot_verify: "Cannot verify",
};
const equipmentKey = (id: string) => ["equipment", id];
const acceptedFiles: Record<EquipmentRole, string> = {
  drawing: ".pdf,.dxf",
  bom: ".xlsx,.csv,.pdf",
  specification: ".pdf",
  markup: ".pdf",
};

/** One quiet entry point, kept current with checks of the actual source files. */
export function EquipmentEntry({ id, open }: { id: string; open: () => void }) {
  const q = useQuery({
    queryKey: equipmentKey(id),
    queryFn: () => api<EquipmentView>(`/orders/${id}/equipment`),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
  const comparison = q.data?.comparison;
  const counts = equipmentCounts(comparison);
  const correction = useEquipmentCorrections(id),
    bundle = newestCorrectedBundle(correction.data);
  const readyFiles = !!bundle && correctedBundleReady(bundle),
    readyOutlook = !!bundle && correctedDraftReady(bundle);
  return (
    <button
      className={`equipment-entry equipment-outcome${readyOutlook ? " is-ready" : ""}`}
      onClick={open}
    >
      <span className="equipment-entry-symbol">
        <FileText size={20} />
      </span>
      <span>
        <span className="equipment-entry-label">Drawing and BOM</span>
        <strong>
          {readyOutlook
            ? "Corrected draft ready in Outlook"
            : readyFiles
              ? "Corrected files prepared and checked"
              : comparison?.checks.length
                ? equipmentVerdict(comparison)
                : "Check the equipment revision"}
        </strong>
        <small>
          {readyFiles
            ? bundle!.native_verification
              ? "Drawing connections, layout and BOM rechecked"
              : `${bundle!.verification.summary.verified} supported fields verified in the corrected files`
            : comparison?.checks.length
              ? comparison.stale
                ? "Documents changed. Check the latest revision."
                : `${counts.verified} verified · ${counts.remaining} remaining · ${counts.cannot_verify} cannot verify${counts.unchanged ? ` · ${counts.unchanged} unchanged` : ""}${comparison.summary.source_issues ? " · Source coverage limited" : ""}`
              : q.isPending
                ? "Reading the current equipment files…"
                : "Compare what changed in the actual drawing and BOM."}
          {readyFiles && (
            <span className="equipment-entry-files">
              {correctedFiles(bundle!).length} corrected{" "}
              {correctedFiles(bundle!).length === 1 ? "file" : "files"}
              {readyOutlook
                ? " · Unsent reply saved"
                : " · Ready for your review"}
            </span>
          )}
        </small>
      </span>
      <span className="equipment-entry-action">
        {readyFiles ? "Open prepared work" : "Review changes"}{" "}
        <ArrowUpRight size={16} />
      </span>
    </button>
  );
}

export function EquipmentReview({ id }: { id: string }) {
  const qc = useQueryClient();
  const corrections = useEquipmentCorrections(id),
    corrected = newestCorrectedBundle(corrections.data);
  const hasCorrected = !!corrected && correctedBundleReady(corrected);
  const q = useQuery({
    queryKey: equipmentKey(id),
    queryFn: () => api<EquipmentView>(`/orders/${id}/equipment`),
    staleTime: 30_000,
  });
  const [baseline, setBaseline] = useState("");
  const [target, setTarget] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showUnchanged, setShowUnchanged] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const [showUpload, setShowUpload] = useState(false);
  const [role, setRole] = useState<EquipmentRole>("drawing");
  const [revision, setRevision] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [existing, setExisting] = useState<string[]>([]);
  const initialized = useRef("");
  useEffect(() => {
    const signature = `${id}:${q.data?.version}:${q.data?.comparison?.fingerprint || ""}`;
    if (q.data && initialized.current !== signature) {
      const pair = equipmentPair(q.data);
      setBaseline(pair[0]);
      setTarget(pair[1]);
      setExpanded(null);
      setShowUnchanged(false);
      initialized.current = signature;
    }
  }, [id, q.data]);
  function update(view: EquipmentView) {
    qc.setQueryData(equipmentKey(id), view);
    void qc.invalidateQueries({ queryKey: ["order-activity", id] });
    void qc.invalidateQueries({ queryKey: ["record", id] });
  }
  async function check() {
    if (!q.data || !baseline || !target || baseline === target || checking)
      return;
    setChecking(true);
    setError("");
    try {
      update(
        await api<EquipmentView>(`/orders/${id}/equipment/check`, {
          expected_version: q.data.version,
          baseline_revision: baseline,
          target_revision: target,
        }),
      );
      setExpanded(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }
  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!revision.trim() || (!files.length && !existing.length) || uploading)
      return;
    setUploading(true);
    setError("");
    const data = new FormData();
    data.set("role", role);
    data.set("revision", revision.trim());
    data.set("document_ids", JSON.stringify(existing));
    for (const file of files) data.append("files", file);
    try {
      update(
        await api<EquipmentView>(`/orders/${id}/equipment/packages`, data),
      );
      setShowUpload(false);
      setFiles([]);
      setExisting([]);
      setRevision("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
    }
  }
  if (q.isPending)
    return (
      <div className="equipment-review">
        <Busy text="Reading the drawing and BOM check…" />
      </div>
    );
  if (!q.data)
    return (
      <div className="equipment-review">
        <ErrorNote message="The equipment check could not load." />
        <button className="secondary" onClick={() => void q.refetch()}>
          Try again
        </button>
      </div>
    );
  const view = q.data,
    comparison = view.comparison,
    counts = equipmentCounts(comparison),
    revisions = equipmentRevisions(view);
  const hasChecks = !!comparison?.checks.length;
  const pairChanged =
    comparison &&
    (baseline !== comparison.baseline_revision ||
      target !== comparison.target_revision);
  return (
    <section
      className="equipment-review"
      aria-label="Drawing and BOM revision check"
    >
      <div className="equipment-intro">
        <p>
          {corrections.data?.plan.native_plan || corrected?.native_verification
            ? "Inspect changed connections, device positions and the BOM. The native view uses the drawing’s actual coordinates."
            : "Rivet checks the equipment values in the actual files. Open a result to see what changed and where it was found."}
        </p>
        <button
          className="equipment-text-action"
          onClick={() => setShowUpload(!showUpload)}
          aria-expanded={showUpload}
        >
          <UploadSimple size={16} />
          Add revision files
        </button>
      </div>
      {error && <ErrorNote message={error} />}
      {q.isError && (
        <ErrorNote message="The latest check could not load. Showing the saved result." />
      )}
      <EquipmentCorrections id={id} />
      <OrderManufacturerProfile id={id} />
      <DrawingModelEvidence id={id} />
      {hasCorrected && (
        <div className="equipment-original-label">
          <span>
            Original package · Revision {q.data.comparison?.target_revision}
          </span>
          <p>
            The issues below belong to the original files. Rivet’s checked draft
            copies are shown above.
          </p>
        </div>
      )}
      {showUpload && (
        <form className="equipment-upload" onSubmit={upload}>
          <div className="equipment-upload-heading">
            <strong>Add the actual issued files</strong>
            <p>Keep the drawing and its BOM under the same revision.</p>
          </div>
          <div className="equipment-upload-fields">
            <label>
              File type
              <select
                value={role}
                disabled={uploading}
                onChange={(e) => setRole(e.target.value as EquipmentRole)}
              >
                <option value="drawing">Drawing</option>
                <option value="bom">Bill of materials</option>
                <option value="specification">
                  Specification / requirement
                </option>
                <option value="markup">Returned markup</option>
              </select>
            </label>
            <label>
              Revision
              <input
                required
                disabled={uploading}
                maxLength={40}
                placeholder="e.g. D"
                value={revision}
                onChange={(e) => setRevision(e.target.value)}
              />
            </label>
          </div>
          <label className="equipment-file-input">
            {role === "drawing"
              ? "PDF or DXF"
              : role === "bom"
                ? "Excel, CSV or PDF"
                : "PDF"}
            <input
              type="file"
              disabled={uploading}
              accept={acceptedFiles[role]}
              multiple
              required={!existing.length}
              onChange={(e) => setFiles(Array.from(e.target.files || []))}
            />
          </label>
          {!!view.available_documents.length && (
            <details className="equipment-existing">
              <summary>
                Use files already on this order
                {existing.length ? ` · ${existing.length} selected` : ""}
              </summary>
              <div>
                {view.available_documents.map((doc) => (
                  <label key={doc.id}>
                    <input
                      type="checkbox"
                      disabled={uploading}
                      checked={existing.includes(doc.id)}
                      onChange={(e) =>
                        setExisting(
                          e.target.checked
                            ? [...existing, doc.id]
                            : existing.filter((id) => id !== doc.id),
                        )
                      }
                    />
                    <span>
                      {doc.name}
                      <small>
                        {equipmentRole(doc.role)}
                        {doc.revision ? ` · Rev ${doc.revision}` : ""}
                      </small>
                    </span>
                  </label>
                ))}
              </div>
            </details>
          )}
          <div className="equipment-upload-actions">
            <button
              type="button"
              className="equipment-text-action"
              onClick={() => setShowUpload(false)}
              disabled={uploading}
            >
              Cancel
            </button>
            <button
              className="primary"
              disabled={
                uploading ||
                !revision.trim() ||
                (!files.length && !existing.length)
              }
            >
              {uploading ? "Reading files…" : "Add and check"}
            </button>
          </div>
        </form>
      )}
      {revisions.length > 1 && (
        <div className="equipment-revision-control">
          <div>
            <label>
              Previous revision
              <select
                value={baseline}
                onChange={(e) => setBaseline(e.target.value)}
              >
                {revisions.map((r) => (
                  <option key={r} value={r}>
                    Rev {r}
                  </option>
                ))}
              </select>
            </label>
            <ArrowDown size={16} />
            <label>
              Revised package
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              >
                {revisions.map((r) => (
                  <option key={r} value={r}>
                    Rev {r}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button
            className="secondary"
            onClick={() => void check()}
            disabled={
              checking ||
              uploading ||
              !baseline ||
              !target ||
              baseline === target
            }
          >
            {checking ? "Checking files…" : "Check revision"}
          </button>
        </div>
      )}
      {baseline && baseline === target && revisions.length > 1 && (
        <p className="equipment-inline-note" role="status">
          Choose two different revisions to compare.
        </p>
      )}
      {pairChanged && (
        <p className="equipment-inline-note" role="status">
          The result below is still Rev {comparison.baseline_revision} → Rev{" "}
          {comparison.target_revision}. Check the selected revisions to update
          it.
        </p>
      )}
      {comparison?.stale && (
        <div className="equipment-stale" role="status">
          The files changed after this check. Re-check the revision before
          relying on these results.
        </div>
      )}
      {!!comparison?.summary.source_issues && (
        <div className="equipment-stale" role="status">
          {comparison.summary.source_issues} source{" "}
          {comparison.summary.source_issues === 1
            ? "issue limits"
            : "issues limit"}{" "}
          this check. Individual verified fields do not establish that the full
          package was read. See the source details below.
        </div>
      )}
      {hasChecks && (
        <>
          <h3 className="equipment-verdict">
            {hasCorrected
              ? "Changes found in the original files"
              : equipmentVerdict(comparison)}
          </h3>
          {!comparison.baseline_revision && (
            <p className="equipment-inline-note" role="status">
              This is the current equipment model. Add the previous revision to
              check which drawing and BOM values changed.
            </p>
          )}
          <div
            className="equipment-summary"
            aria-label="Revision check results"
          >
            <div className="is-verified">
              <strong>{counts.verified}</strong>
              <span>Verified</span>
            </div>
            <div>
              <strong>{counts.remaining}</strong>
              <span>Remaining</span>
            </div>
            <div>
              <strong>{counts.cannot_verify}</strong>
              <span>Cannot verify</span>
            </div>
          </div>
          <div className="equipment-result-heading">
            <h3>
              {comparison.baseline_revision ? (
                <>
                  Rev {comparison.baseline_revision} <span>→</span> Rev{" "}
                  {comparison.target_revision}
                </>
              ) : (
                <>Revision {comparison.target_revision} equipment model</>
              )}
            </h3>
            <span>
              {comparison.model?.length
                ? `${comparison.model.length} equipment tags · `
                : ""}
              {comparison.checks.length} field checks
              {counts.unchanged ? ` · ${counts.unchanged} unchanged` : ""}
              {comparison.checked_at ? ` · ${when(comparison.checked_at)}` : ""}
            </span>
          </div>
          <div className="equipment-table-scroll">
            <table className="equipment-table">
              <thead>
                <tr>
                  <th scope="col">Equipment / field</th>
                  <th scope="col">
                    {comparison.baseline_revision
                      ? `Drawing · ${comparison.baseline_revision}`
                      : "Previous drawing"}
                  </th>
                  <th scope="col">Drawing · {comparison.target_revision}</th>
                  <th scope="col">BOM · {comparison.target_revision}</th>
                  <th scope="col">Required</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {equipmentChecks(comparison)
                  .filter(
                    (item) => showUnchanged || item.status !== "unchanged",
                  )
                  .map((item) => (
                    <Fragment key={item.id}>
                      <tr className={expanded === item.id ? "is-expanded" : ""}>
                        <th scope="row">
                          <button
                            className="equipment-row-toggle"
                            onClick={() =>
                              setExpanded(expanded === item.id ? null : item.id)
                            }
                            aria-expanded={expanded === item.id}
                            aria-controls={`equipment-check-${item.id}`}
                          >
                            <span>
                              <strong>
                                {item.device_tag || "Unmatched equipment"}
                              </strong>
                              <small>
                                {item.label?.startsWith(`${item.device_tag} · `)
                                  ? item.label.slice(item.device_tag.length + 3)
                                  : item.label ||
                                    item.field.replaceAll("_", " ")}
                              </small>
                            </span>
                            <CaretDown size={13} />
                          </button>
                        </th>
                        <td
                          data-label={`Drawing · ${comparison.baseline_revision || "Previous"}`}
                        >
                          {comparison.baseline_revision
                            ? equipmentValue(
                                item.baseline_drawing?.value,
                                item.field,
                              )
                            : "Not provided"}
                        </td>
                        <td
                          data-label={`Drawing · ${comparison.target_revision}`}
                        >
                          {equipmentValue(
                            item.target_drawing?.value,
                            item.field,
                          )}
                        </td>
                        <td data-label={`BOM · ${comparison.target_revision}`}>
                          {equipmentValue(item.target_bom?.value, item.field)}
                        </td>
                        <td data-label="Required">
                          {item.required?.value == null
                            ? "Not supplied"
                            : equipmentValue(item.required.value, item.field)}
                        </td>
                        <td>
                          <span
                            className={`equipment-status is-${equipmentStatus(item.status, comparison.stale)}`}
                          >
                            {equipmentStatus(item.status, comparison.stale) ===
                              "verified" && <Check size={12} />}
                            {
                              statusLabels[
                                equipmentStatus(item.status, comparison.stale)
                              ]
                            }
                          </span>
                        </td>
                      </tr>
                      {expanded === item.id && (
                        <tr className="equipment-expanded-row">
                          <td colSpan={6}>
                            <div id={`equipment-check-${item.id}`}>
                              <CheckDetails
                                id={id}
                                check={item}
                                stale={comparison.stale}
                              />
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
              </tbody>
            </table>
          </div>
          {counts.unchanged > 0 && (
            <button
              type="button"
              className="equipment-text-action equipment-unchanged-toggle"
              onClick={() => setShowUnchanged(!showUnchanged)}
              aria-expanded={showUnchanged}
            >
              {showUnchanged
                ? "Hide unchanged fields"
                : `Show ${counts.unchanged} unchanged fields`}
            </button>
          )}
          <p className="equipment-result-note">
            Verified applies to the field and sources shown. This check does not
            approve engineering, confirm every component was extracted, or
            release a drawing for production.
          </p>
        </>
      )}
      {!hasChecks && (
        <div className="equipment-empty">
          <FileText size={28} />
          <h3>
            {revisions.length < 2
              ? "Add the next revision to close the loop"
              : "Ready to compare the issued files"}
          </h3>
          <p>
            {revisions.length < 2
              ? "Add a baseline drawing, the revised drawing and its BOM. Add the specification or returned markup to show what was required."
              : "Choose the previous and revised package, then check what changed in the drawing and BOM."}
          </p>
          {!showUpload && (
            <button className="secondary" onClick={() => setShowUpload(true)}>
              Add revision files
            </button>
          )}
        </div>
      )}
      {!!comparison?.warnings?.length && (
        <details className="equipment-warnings">
          <summary>
            Source coverage notes · {comparison.warnings.length}
          </summary>
          <ul>
            {comparison.warnings.map((warning, i) => (
              <li key={i}>{warning}</li>
            ))}
          </ul>
        </details>
      )}
      {!!view.packages.length && (
        <details className="equipment-inventory">
          <summary>
            Files used in this equipment model{" "}
            <span>
              {view.packages.reduce((n, p) => n + p.documents.length, 0)}
            </span>
          </summary>
          {view.packages.map((pack) => (
            <div className="equipment-package" key={pack.revision}>
              <strong>Revision {pack.revision}</strong>
              <ul>
                {pack.documents.map((doc) => (
                  <li key={doc.id}>
                    <FileText size={15} />
                    <span>
                      {doc.name}
                      <small>
                        {equipmentRole(doc.role)}
                        {doc.state ? ` · ${doc.state}` : ""}
                      </small>
                    </span>
                    <OriginalFile
                      orderId={id}
                      documentId={doc.id}
                      name={doc.name}
                    />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </details>
      )}
      {!!view.limitations.length && (
        <details className="equipment-limitations">
          <summary>Coverage and limits</summary>
          <ul>
            {view.limitations.map((text, i) => (
              <li key={i}>{text}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function CheckDetails({
  id,
  check,
  stale,
}: {
  id: string;
  check: EquipmentCheck;
  stale?: boolean;
}) {
  const [selected, setSelected] = useState<EquipmentEvidence | null>(null);
  const cells: [string, EquipmentCell][] = [
    ["Previous drawing", check.baseline_drawing],
    ["Revised drawing", check.target_drawing],
    ["Revised BOM", check.target_bom],
    ["Requirement", check.required],
  ];
  return (
    <div className="equipment-check-detail">
      <p className="equipment-check-reason">
        {stale
          ? "The source files changed after this result. Re-check the revision to verify this field."
          : check.reason}
      </p>
      <div className="equipment-evidence-grid">
        {cells.map(([label, cell]) => (
          <div key={label}>
            <span className="equipment-evidence-label">{label}</span>
            <strong>
              {label === "Requirement" && cell?.value == null
                ? "Not supplied"
                : equipmentValue(cell?.value, check.field)}
            </strong>
            {cell?.evidence?.length ? (
              equipmentSources(cell, check.field).map((e, i) => (
                <button
                  className={`equipment-evidence-link${selected === e ? " is-selected" : ""}`}
                  key={`${e.document_id}:${i}`}
                  onClick={() => setSelected(selected === e ? null : e)}
                >
                  <FileText size={13} />
                  <span>
                    {e.name}
                    <small>{equipmentLocation(e) || "Document evidence"}</small>
                  </span>
                </button>
              ))
            ) : (
              <small className="equipment-no-evidence">No source located</small>
            )}
          </div>
        ))}
      </div>
      {selected && (
        <div className="equipment-source-preview">
          <div className="equipment-source-heading">
            <span>
              <strong>{selected.name}</strong>
              <small>{equipmentLocation(selected)}</small>
            </span>
            <OriginalFile
              orderId={id}
              documentId={selected.document_id}
              name={selected.name}
              page={selected.page}
            />
          </div>
          {selected.excerpt && <blockquote>{selected.excerpt}</blockquote>}
          <EvidenceImage
            key={`${selected.document_id}:${selected.page}:${selected.crop_url || ""}`}
            orderId={id}
            evidence={selected}
          />
        </div>
      )}
    </div>
  );
}

function OriginalFile({
  orderId,
  documentId,
  name,
  page,
}: {
  orderId: string;
  documentId: string;
  name: string;
  page?: number | null;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function open() {
    setBusy(true);
    setError("");
    const previewable = /\.(pdf|txt|csv)$/i.test(name),
      tab = previewable ? window.open("", "_blank") : null;
    if (tab) tab.opener = null;
    try {
      const response = await apiFetch(
        `/api/orders/${encodeURIComponent(orderId)}/equipment/files/${encodeURIComponent(documentId)}`,
      );
      if (!response.ok)
        throw new Error("This original file could not be opened.");
      const url = URL.createObjectURL(await response.blob());
      if (tab)
        tab.location.href = `${url}${/\.pdf$/i.test(name) && page ? `#page=${page}` : ""}`;
      else if (previewable) {
        URL.revokeObjectURL(url);
        throw new Error(
          "Allow this file to open in a new tab, then try again.",
        );
      } else {
        const link = document.createElement("a");
        link.href = url;
        link.download = name;
        link.click();
      }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      tab?.close();
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="equipment-original">
      <button
        className="equipment-text-action"
        disabled={busy}
        onClick={() => void open()}
        aria-label={`Open original ${name}`}
      >
        {busy ? "Opening…" : "Open original"}
        <ArrowUpRight size={13} />
      </button>
      {error && <span role="alert">{error}</span>}
    </span>
  );
}

function EvidenceImage({
  orderId,
  evidence,
}: {
  orderId: string;
  evidence: EquipmentEvidence;
}) {
  const [url, setUrl] = useState(""),
    [error, setError] = useState("");
  const path = equipmentEvidencePath(orderId, evidence);
  useEffect(() => {
    setUrl("");
    setError("");
    if (!path) return;
    const abort = new AbortController();
    let objectUrl = "";
    void (async () => {
      try {
        const res = await apiFetch(path, { signal: abort.signal });
        if (!res.ok)
          throw new Error(
            "A page preview is not available. Open the original to inspect this location.",
          );
        objectUrl = URL.createObjectURL(await res.blob());
        if (!abort.signal.aborted) setUrl(objectUrl);
        else URL.revokeObjectURL(objectUrl);
      } catch (e) {
        if (!abort.signal.aborted) setError((e as Error).message);
      }
    })();
    return () => {
      abort.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);
  if (!path) return null;
  if (error) return <p className="equipment-inline-note">{error}</p>;
  return url ? (
    <img
      className="equipment-page-image"
      src={url}
      alt={`${evidence.name}, ${equipmentLocation(evidence)}. Source page for this equipment value.`}
    />
  ) : (
    <p role="status" className="equipment-inline-note">
      Loading source page…
    </p>
  );
}
