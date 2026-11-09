import { useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  DownloadSimple,
  FileText,
  WarningCircle,
} from "@phosphor-icons/react";
import { api, apiFetch, when } from "./api";
import { Busy, ErrorNote } from "./ui";
import { RecordUpload } from "./RecordWorkspace";
import type { RecordView } from "./record-types";
import { safeOutlookUrl } from "./outlook-workflow";
import "./engineering-workspace.css";

type Ref = { source_id: string; page?: number; url?: string; text?: string };
type Item = {
  id: string;
  key: string;
  value: unknown;
  subject_id: string;
  unit?: string;
  confirmed: boolean;
  dismissed?: boolean;
  source_refs: Ref[];
  excluded?: boolean;
  raw_confidence?: string;
  unresolved_role?: boolean;
  origin_role?: string;
};
type CheckResult = {
  rule_id: string;
  status: string;
  reason: string;
  source_refs: Ref[];
};
type Question = { id: string; key: string; reason: string };
type Position = {
  id: string;
  device_id: string;
  side?: string;
  start_in?: number | null;
  mounting_space_in?: number | null;
};
type Design = {
  id: string;
  model_version: number;
  family: string;
  fully_configured: boolean;
  sections: {
    id: string;
    kind: string;
    width_in?: number;
    positions: Position[];
  }[];
  devices: {
    id: string;
    tag: string;
    frame?: string;
    frame_a?: number;
    trip_a?: number;
    rated_current_a?: number;
    role?: string;
    position_id?: string;
    section_id?: string;
    manufacturer?: string;
    family?: string;
    part_number?: string;
    supplied?: boolean;
  }[];
  rule_results: CheckResult[];
  unresolved_questions: Question[];
};
type DesignValidation = {
  rule_results: CheckResult[];
  unresolved_questions: Question[];
  has_known_rule_violations: boolean;
  fully_configured: boolean;
};
type CutSheetMapping = {
  document_id: string;
  component_ids: string[];
  pages: number[];
  identity: Record<string, string>;
  verified: boolean;
};
type GenerationSettings = {
  template?: {
    id: string;
    version: string;
    library_document_id: string;
    blocks: { breaker: string; title_block: string };
    elevation_mode: string;
  } | null;
  cut_sheets: CutSheetMapping[];
  reason: string;
};
type EquipmentObservation = {
  id: string;
  source_id: string;
  source_sha: string;
  page: number;
  source_object_key: string;
  display_tag?: string;
  raw_label?: string;
  role?: string;
  unresolved_role: boolean;
  values: { key: string; value: unknown; unit?: string }[];
};
type Artifact = { name: string; kind: string; sha256: string; url: string };
type Package = {
  id: string;
  model_version: number;
  artifacts: Artifact[];
  document_ids?: string[];
  unresolved_geometry: unknown[];
};
type Engineering = {
  order_id: string;
  version: number;
  model_id: string;
  model_version: number;
  number: string;
  title: string;
  stale: boolean;
  documents: {
    id: string;
    name: string;
    state: string;
    role: string;
    url: string;
  }[];
  requirements: null | {
    items: Item[];
    conflicts: {
      id: string;
      key: string;
      reason: string;
      item_ids: string[];
    }[];
    unreadable_pages: unknown[];
    warnings: unknown[];
  };
  configuration: null | {
    configurations: Design[];
    unresolved_questions: Question[];
  };
  selected_design: Design | null;
  packages: Package[];
  generation_settings?: GenerationSettings;
  equipment_observations?: EquipmentObservation[];
  requirement_fields?: string[];
  review_comments?: {
    id: string;
    text: string;
    revision?: string;
    source_refs: { id: string; document_id: string; page?: number }[];
  }[];
  review_deliveries?: {
    id: string;
    package_id: string;
    model_version: number;
    comment_id: string;
    requirement_id: string;
    created_at: string;
    status: string;
  }[];
  outlook_deliveries?: Record<
    string,
    {
      status: string;
      web_link?: string;
      mailbox?: string;
      error?: string;
      attachments_verified?: boolean;
    }
  >;
  precheck?: {
    model_version: number;
    validation: { rule_results: CheckResult[] };
    customer_memory_note: string;
    customer_one_line_note: string;
    customer_preferences: { title: string }[];
    saved_drawing_bom_check?: {
      status: string;
      [key: string]: unknown;
    };
  };
  approval?: { signer: string; at: string; model_version: number };
  release_export?: {
    package_id: string;
    model_version: number;
    files: string[];
  };
  history: {
    version: number;
    summary: string;
    actor: string;
    created_at: string;
  }[];
};
const stages = [
  "requirements",
  "configure",
  "drawings",
  "review",
  "release",
] as const;
type Stage = (typeof stages)[number];
const titles = {
  requirements: "Requirements",
  configure: "Configure",
  drawings: "Drawings",
  review: "Review",
  release: "Release",
};
const roles = [
  ["unclassified", "Choose source role"],
  ["specification", "Customer specification"],
  ["customer_one_line", "Engineer’s one-line"],
  ["addendum", "Customer addendum"],
  ["quote_exception", "Quote exception"],
  ["purchase_order", "Purchase order"],
  ["manufacturer_reference", "Manufacturer reference / DXF block library"],
  ["component_cut_sheet", "Component cut sheet"],
];
const label = (key: string) => key.replaceAll("_", " ").replaceAll(".", " · ");
const display = (value: unknown) =>
  value === null
    ? "Unknown"
    : typeof value === "boolean"
      ? value
        ? "Required"
        : "Not required"
      : String(value);

export function engineeringAssignmentPair(
  design: Design,
  firstId: string,
  secondId: string,
) {
  const first = design.devices.find((device) => device.id === firstId);
  const second = design.devices.find((device) => device.id === secondId);
  const firstSection = design.sections.find(
    (section) => section.id === first?.section_id,
  );
  const secondSection = design.sections.find(
    (section) => section.id === second?.section_id,
  );
  const firstPosition = firstSection?.positions.find(
    (position) =>
      position.id === first?.position_id && position.device_id === firstId,
  );
  const secondPosition = secondSection?.positions.find(
    (position) =>
      position.id === second?.position_id && position.device_id === secondId,
  );
  if (
    !first ||
    !second ||
    firstId === secondId ||
    first.role !== "feeder" ||
    second.role !== "feeder" ||
    firstSection?.kind !== "distribution" ||
    secondSection?.kind !== "distribution" ||
    !first.frame ||
    first.frame !== second.frame ||
    !firstPosition ||
    !secondPosition ||
    !firstPosition.mounting_space_in ||
    firstPosition.mounting_space_in !== secondPosition.mounting_space_in
  ) {
    return null;
  }
  return { firstSection, secondSection, firstPosition, secondPosition };
}

export function swapEngineeringAssignments(
  design: Design,
  firstId: string,
  secondId: string,
): Design {
  const pair = engineeringAssignmentPair(design, firstId, secondId);
  if (!pair) return design;
  const { firstSection, secondSection, firstPosition, secondPosition } = pair;
  return {
    ...design,
    devices: design.devices.map((device) =>
      device.id === firstId
        ? {
            ...device,
            section_id: secondSection.id,
            position_id: secondPosition.id,
          }
        : device.id === secondId
          ? {
              ...device,
              section_id: firstSection.id,
              position_id: firstPosition.id,
            }
          : device,
    ),
    sections: design.sections.map((section) => ({
      ...section,
      positions: section.positions.map((position) =>
        position.id === firstPosition.id
          ? { ...position, device_id: secondId }
          : position.id === secondPosition.id
            ? { ...position, device_id: firstId }
            : position,
      ),
    })),
  };
}

export function engineeringOutlookCanRetry(status?: string) {
  return (
    status === undefined ||
    status === "failed" ||
    status === "permission_required"
  );
}

export function engineeringReviewRequirementSupported(
  item: Item,
  design: Design | null,
) {
  if (
    !design ||
    item.dismissed ||
    item.excluded ||
    item.unresolved_role ||
    item.origin_role === "reference"
  )
    return false;
  const deviceFields = new Set([
    "frame_a",
    "trip_a",
    "poles",
    "trip_type",
    "nameplate",
    "part_number",
  ]);
  const systemFields = new Set([
    "service_rating_a",
    "voltage_v",
    "voltage_to_ground_v",
    "frequency_hz",
    "phases",
    "wires",
    "available_fault_ka",
    "minimum_interrupting_ka",
    "short_time_withstand_ka",
    "current_type",
    "grounding",
    "service_disconnect",
    "code_edition",
  ]);
  if (item.subject_id === "order") {
    if (systemFields.has(item.key)) return true;
    return (
      item.key.startsWith("main.") &&
      deviceFields.has(item.key.slice(5)) &&
      design.devices.filter((device) => device.role === "main").length === 1
    );
  }
  if (!deviceFields.has(item.key)) return false;
  const exact = design.devices.filter(
    (device) => device.id === item.subject_id,
  );
  return (
    (exact.length
      ? exact
      : design.devices.filter((device) => device.tag === item.subject_id)
    ).length === 1
  );
}

async function openFile(path: string, name?: string, page?: number) {
  const response = await apiFetch("/api" + path);
  if (!response.ok)
    throw new Error("The source file is unavailable. Refresh and try again.");
  const object = URL.createObjectURL(await response.blob());
  const a = document.createElement("a");
  a.href = object + (page ? `#page=${page}` : "");
  a.target = "_blank";
  a.rel = "noopener";
  if (name) a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(object), 60_000);
}

export function EngineeringWorkspace({
  id,
  navigate,
  notify,
  review,
}: {
  id: string;
  navigate: (to: string) => void;
  notify: (text: string) => void;
  review: ReactNode;
}) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["engineering", id],
    queryFn: () => api<Engineering>(`/orders/${id}/engineering`),
  });
  const [busy, setBusy] = useState(""),
    [error, setError] = useState("");
  const [classifications, setClassifications] = useState<
    Record<string, string>
  >({});
  const [values, setValues] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [dismissed, setDismissed] = useState<Record<string, boolean>>({});
  const [reason, setReason] = useState("");
  const [signer, setSigner] = useState("");
  const [editedDesign, setEditedDesign] = useState<Design | null>(null);
  const [preview, setPreview] = useState<{
    design: Design;
    validation: DesignValidation;
  } | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [rulesText, setRulesText] = useState("");
  const [upload, setUpload] = useState(false);
  const [reviewComment, setReviewComment] = useState("");
  const [reviewEvidence, setReviewEvidence] = useState("");
  const [reviewRequirement, setReviewRequirement] = useState("");
  const [reviewValue, setReviewValue] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const record = useQuery({
    queryKey: ["record", id],
    queryFn: () => api<RecordView>(`/orders/${id}/record`),
    enabled: upload,
  });
  useEffect(() => {
    setClassifications({});
    setValues({});
    setSelected({});
    setDismissed({});
    setReason("");
    setSigner("");
    setEditedDesign(null);
    setRulesText("");
    setError("");
    setUpload(false);
    setReviewComment("");
    setReviewEvidence("");
    setReviewRequirement("");
    setReviewValue("");
    setReviewReason("");
  }, [id]);
  const d = query.data;
  useEffect(() => {
    setEditedDesign(null);
    setPreview(null);
    setPreviewError("");
  }, [id, d?.version, d?.selected_design?.id]);
  useEffect(() => {
    if (!editedDesign || !d) {
      setPreviewBusy(false);
      return;
    }
    let current = true;
    setPreviewBusy(true);
    setPreviewError("");
    const timer = setTimeout(() => {
      void api<{ validation: DesignValidation }>(
        `/orders/${id}/engineering/design-preview`,
        {
          expected_version: d.version,
          design: editedDesign,
        },
      )
        .then((result) => {
          if (current)
            setPreview({ design: editedDesign, validation: result.validation });
        })
        .catch((failure: Error) => {
          if (current) setPreviewError(failure.message);
        })
        .finally(() => {
          if (current) setPreviewBusy(false);
        });
    }, 350);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [id, d?.version, editedDesign]);
  const editing = editedDesign ?? d?.selected_design;
  const currentPreview =
    preview?.design === editedDesign ? preview.validation : null;
  function editRating(
    deviceId: string,
    field: "trip_a" | "frame_a" | "rated_current_a",
    value: string,
  ) {
    if (!editing) return;
    setEditedDesign({
      ...editing,
      devices: editing.devices.map((device) =>
        device.id === deviceId
          ? { ...device, [field]: value === "" ? null : Number(value) }
          : device,
      ),
    });
  }
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const legacyLink =
    params.has("source") ||
    params.has("comment") ||
    params.has("tab") ||
    params.has("view");
  const requested = params.get("stage") as Stage;
  const stage: Stage = legacyLink
    ? "review"
    : stages.includes(requested)
      ? requested
      : d?.model_version
        ? "requirements"
        : d?.documents.length
          ? "review"
          : "requirements";
  function go(next: Stage) {
    navigate(`order/${id}?stage=${next}`);
  }
  async function act(action: string, body: object = {}, next?: Stage) {
    if (!d) return;
    setBusy(action);
    setError("");
    try {
      const result = await api<Engineering>(
        `/orders/${id}/engineering/${action}`,
        { expected_version: d.version, ...body },
      );
      qc.setQueryData(["engineering", id], result);
      await qc.invalidateQueries({ queryKey: ["record", id] });
      await qc.invalidateQueries({ queryKey: ["equipment", id] });
      if (next) go(next);
      notify("Saved to order history");
      return result;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  const currentPackage = d?.packages
    .slice()
    .reverse()
    .find((p) => p.model_version === d.model_version);
  const chosenComment = d?.review_comments?.find(
    (comment) => comment.id === reviewComment,
  );
  async function saveReviewOutlook(deliveryId: string) {
    if (!d || busy) return;
    setBusy(`review-outlook:${deliveryId}`);
    setError("");
    try {
      await api(`/orders/${id}/equipment/corrections/${deliveryId}/outlook`, {
        expected_version: d.version,
      });
      notify("Outlook handoff updated");
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      await Promise.all(
        [
          ["engineering", id],
          ["record", id],
          ["equipment-corrections", id],
          ["order-activity", id],
          ["outlook-lifecycle"],
        ].map((queryKey) => qc.invalidateQueries({ queryKey })),
      );
      setBusy("");
    }
  }
  const source = (ref: Ref) =>
    ref.url ? (
      <a href={ref.url} target="_blank" rel="noreferrer">
        Source p. {ref.page}
      </a>
    ) : (
      <button
        className="text-button"
        type="button"
        onClick={() =>
          void openFile(
            `/documents/${ref.source_id}/content`,
            undefined,
            ref.page,
          ).catch((e) => setError(e.message))
        }
      >
        p. {ref.page ?? "?"}
      </button>
    );
  if (query.isPending) return <Busy text="Opening engineering order…" />;
  if (!d)
    return (
      <ErrorNote
        message={query.error?.message || "The order could not be opened."}
      />
    );
  const canApply = !!(
    currentPackage &&
    d.review_comments?.length &&
    d.requirements
  );
  return (
    <section className="engineering-order">
      <div className="order-tools-return">
        <button className="text-button" onClick={() => navigate(`order/${id}`)}>
          Back to order review
        </button>
        <span>Engineering tools</span>
      </div>
      <nav className="engineering-stages" aria-label="Order stages">
        {stages.map((s, i) => (
          <button
            key={s}
            aria-current={s === stage ? "step" : undefined}
            className={s === stage ? "active" : ""}
            onClick={() => go(s)}
          >
            <span>{i + 1}</span>
            {titles[s]}
          </button>
        ))}
        <small>Proposed model {d.model_version || "—"}</small>
      </nav>
      {error && (
        <div className="engineering-error">
          <ErrorNote message={error} />
        </div>
      )}
      {stage === "review" ? (
        <>
          {review}
          {(canApply || !!d.review_deliveries?.length) && (
            <div className="engineering-stage">
              {canApply && (
                <section className="engineering-block">
                  <h2>Apply a reviewed comment to the model</h2>
                  <p>
                    Choose the exact comment evidence, requirement and
                    replacement value. This records a model version and prepares
                    new files for review.
                  </p>
                  {currentPackage &&
                  d.review_comments?.length &&
                  d.requirements ? (
                    <form
                      onSubmit={(event) => {
                        event.preventDefault();
                        void act("review-change", {
                          package_id: currentPackage.id,
                          comment_id: reviewComment,
                          evidence_id: reviewEvidence,
                          requirement_id: reviewRequirement,
                          value: reviewValue,
                          reason: reviewReason,
                        }).then((result) => {
                          if (result) {
                            setReviewComment("");
                            setReviewEvidence("");
                            setReviewRequirement("");
                            setReviewValue("");
                            setReviewReason("");
                          }
                        });
                      }}
                    >
                      <div className="engineering-form-grid">
                        <label>
                          Review comment
                          <select
                            value={reviewComment}
                            required
                            onChange={(event) => {
                              setReviewComment(event.target.value);
                              setReviewEvidence("");
                            }}
                          >
                            <option value="">
                              Choose the comment being addressed
                            </option>
                            {d.review_comments.map((comment) => (
                              <option key={comment.id} value={comment.id}>
                                {comment.revision
                                  ? `${comment.revision} · `
                                  : ""}
                                {comment.text.slice(0, 180)}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          Exact comment evidence
                          <select
                            required
                            value={reviewEvidence}
                            onChange={(event) =>
                              setReviewEvidence(event.target.value)
                            }
                          >
                            <option value="">Choose source evidence</option>
                            {chosenComment?.source_refs.map((ref) => (
                              <option key={ref.id} value={ref.id}>
                                {d.documents.find(
                                  (document) => document.id === ref.document_id,
                                )?.name || "Comment source"}{" "}
                                · p. {ref.page ?? "?"}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          Requirement to revise
                          <select
                            required
                            value={reviewRequirement}
                            onChange={(event) =>
                              setReviewRequirement(event.target.value)
                            }
                          >
                            <option value="">
                              Choose the requirement this comment changes
                            </option>
                            {d.requirements.items
                              .filter((item) =>
                                engineeringReviewRequirementSupported(
                                  item,
                                  d.selected_design,
                                ),
                              )
                              .map((item) => (
                                <option key={item.id} value={item.id}>
                                  {item.subject_id} · {label(item.key)} ·{" "}
                                  {display(item.value)}
                                </option>
                              ))}
                          </select>
                        </label>
                        <label>
                          Reviewed replacement value
                          <input
                            required
                            value={reviewValue}
                            onChange={(event) =>
                              setReviewValue(event.target.value)
                            }
                          />
                        </label>
                      </div>
                      {chosenComment && <p>{chosenComment.text}</p>}
                      <p>
                        This action supports system scalars and exact device
                        frame, trip, poles, trip type, nameplate and part text.
                        Resolve reference, excluded or ambiguous equipment
                        requirements in Requirements before revising the model.
                      </p>
                      {chosenComment?.source_refs
                        .filter((ref) => ref.id === reviewEvidence)
                        .map((ref) => (
                          <button
                            type="button"
                            key={ref.id}
                            onClick={() =>
                              void openFile(
                                `/documents/${ref.document_id}/content`,
                                undefined,
                                ref.page,
                              ).catch((failure: Error) =>
                                setError(failure.message),
                              )
                            }
                          >
                            Open selected comment evidence
                          </button>
                        ))}
                      <label className="engineering-review-note">
                        Engineering change reason
                        <input
                          required
                          minLength={8}
                          value={reviewReason}
                          onChange={(event) =>
                            setReviewReason(event.target.value)
                          }
                          placeholder="Explain the checked comment-to-requirement change"
                        />
                      </label>
                      <div className="engineering-actions">
                        <button
                          type="submit"
                          disabled={
                            !!busy ||
                            !!editedDesign ||
                            !reviewComment ||
                            !reviewEvidence ||
                            !reviewRequirement ||
                            !reviewValue.trim() ||
                            reviewReason.trim().length < 8
                          }
                        >
                          {busy === "review-change"
                            ? "Updating model and preparing files…"
                            : "Apply comment and prepare revised package"}
                        </button>
                      </div>
                    </form>
                  ) : (
                    <p>
                      {!currentPackage
                        ? "Generate the current model and start revision review to apply a comment."
                        : "No cited review comments are available for this package yet."}
                    </p>
                  )}
                </section>
              )}
              {!!d.review_deliveries?.length && (
                <section className="engineering-block">
                  <h2>Revised review packages</h2>
                  {d.review_deliveries
                    .slice()
                    .reverse()
                    .map((delivery) => {
                      const pack = d.packages.find(
                        (item) => item.id === delivery.package_id,
                      );
                      const outlook = d.outlook_deliveries?.[delivery.id];
                      const link = safeOutlookUrl(outlook?.web_link);
                      const current =
                        delivery.model_version === d.model_version &&
                        currentPackage?.id === delivery.package_id;
                      return (
                        <article
                          className="engineering-cut-sheet"
                          key={delivery.id}
                        >
                          <h3>
                            Model {delivery.model_version} ·{" "}
                            {current
                              ? "Current prepared files"
                              : "Earlier model"}
                          </h3>
                          <p>
                            {when(delivery.created_at)} ·{" "}
                            {label(delivery.status)}
                            {outlook
                              ? ` · Outlook: ${label(outlook.status)}`
                              : ""}
                          </p>
                          {pack && (
                            <ArtifactList
                              artifacts={pack.artifacts}
                              onError={setError}
                            />
                          )}
                          {outlook?.error && (
                            <ErrorNote message={outlook.error} />
                          )}
                          <div className="engineering-actions">
                            {link && (
                              <a href={link} target="_blank" rel="noreferrer">
                                Open in Outlook
                              </a>
                            )}
                            <button
                              disabled={
                                !!busy ||
                                !!editedDesign ||
                                !current ||
                                delivery.status !== "prepared" ||
                                !engineeringOutlookCanRetry(outlook?.status)
                              }
                              onClick={() =>
                                void saveReviewOutlook(delivery.id)
                              }
                            >
                              {busy === `review-outlook:${delivery.id}`
                                ? "Saving unsent draft…"
                                : outlook
                                  ? "Retry unsent Outlook draft"
                                  : "Save unsent Outlook draft"}
                            </button>
                          </div>
                        </article>
                      );
                    })}
                </section>
              )}
            </div>
          )}
        </>
      ) : (
        <main className="page engineering-stage">
          <header className="engineering-heading">
            <div>
              <p>
                {d.number} · {titles[stage]}
              </p>
              <h1>{d.title}</h1>
            </div>
            <span className="engineering-proposed">
              {d.approval?.model_version === d.model_version
                ? "Engineer approval recorded"
                : "Proposed · engineer review required"}
            </span>
          </header>
          {d.stale && (
            <p className="engineering-warning">
              <WarningCircle size={18} />
              Customer inputs changed. Extract and confirm current requirements
              before continuing.
            </p>
          )}
          {editedDesign && stage !== "configure" && (
            <p className="engineering-warning">
              Configuration edits are unsaved.{" "}
              <button onClick={() => go("configure")}>Return to edits</button>
            </p>
          )}
          {stage === "requirements" && (
            <>
              <section className="engineering-block">
                <div className="engineering-section-heading">
                  <div>
                    <h2>Customer inputs</h2>
                    <p>
                      Classify the specification, engineer’s one-line, addenda,
                      exceptions and PO. Manufacturer drawings stay comparison
                      evidence.
                    </p>
                  </div>
                  <button onClick={() => setUpload(true)}>
                    Add source files
                  </button>
                </div>
                {d.documents.length ? (
                  <div className="engineering-sources">
                    {d.documents
                      .filter((doc) => !doc.name.startsWith("GEN-"))
                      .map((doc) => (
                        <div key={doc.id}>
                          <button
                            className="text-button"
                            onClick={() =>
                              void openFile(
                                `/documents/${doc.id}/content`,
                              ).catch((e) => setError(e.message))
                            }
                          >
                            <FileText size={17} />
                            {doc.name}
                          </button>
                          <select
                            aria-label={`Source role for ${doc.name}`}
                            value={classifications[doc.id] ?? doc.role}
                            onChange={(e) =>
                              setClassifications({
                                ...classifications,
                                [doc.id]: e.target.value,
                              })
                            }
                          >
                            {roles.map(([value, name]) => (
                              <option key={value} value={value}>
                                {name}
                              </option>
                            ))}
                          </select>
                        </div>
                      ))}
                  </div>
                ) : (
                  <p>No source files yet.</p>
                )}
                <div className="engineering-actions">
                  <button
                    disabled={!!busy || !Object.keys(classifications).length}
                    onClick={() =>
                      void act("sources", {
                        source_roles: classifications,
                      }).then((result) => {
                        if (result) setClassifications({});
                      })
                    }
                  >
                    Save source roles
                  </button>
                  <button
                    className="primary"
                    disabled={!!busy || Object.keys(classifications).length > 0}
                    onClick={() => void act("extract")}
                  >
                    {busy === "extract"
                      ? "Reading inputs…"
                      : "Extract requirements"}
                  </button>
                </div>
              </section>
              <MissingRequirementForm
                key={`missing:${id}:${d.version}`}
                engineering={d}
                busy={!!busy}
                save={(requirement) => act("requirements/add", requirement)}
                openSource={(sourceId, page) =>
                  void openFile(
                    `/documents/${sourceId}/content`,
                    undefined,
                    page,
                  ).catch((failure: Error) => setError(failure.message))
                }
              />
              {!!d.equipment_observations?.length && (
                <details className="engineering-block">
                  <summary>
                    Source equipment roles ·{" "}
                    {
                      d.equipment_observations.filter(
                        (observation) => observation.unresolved_role,
                      ).length
                    }{" "}
                    unresolved groups
                  </summary>
                  <p>
                    Confirm the role of each source object once. A bare current
                    rating does not establish a breaker frame or trip rating.
                    Reference objects remain visible and are excluded from the
                    configured equipment.
                  </p>
                  {d.equipment_observations.map((observation) => (
                    <EquipmentRoleDecision
                      key={`${observation.id}:${d.version}`}
                      observation={observation}
                      documentName={
                        d.documents.find(
                          (document) => document.id === observation.source_id,
                        )?.name
                      }
                      busy={!!busy}
                      openSource={() =>
                        void openFile(
                          `/documents/${observation.source_id}/content`,
                          undefined,
                          observation.page,
                        ).catch((failure: Error) => setError(failure.message))
                      }
                      decide={(role, reason) =>
                        act("equipment-role", {
                          source_id: observation.source_id,
                          source_sha: observation.source_sha,
                          page: observation.page,
                          source_object_key: observation.source_object_key,
                          role,
                          reason,
                        })
                      }
                    />
                  ))}
                </details>
              )}
              {d.requirements && (
                <section className="engineering-block">
                  <div className="engineering-section-heading">
                    <div>
                      <h2>Requirements to confirm</h2>
                      <p>
                        {
                          d.requirements.items.filter((i) => !i.dismissed)
                            .length
                        }{" "}
                        proposed values · {d.requirements.conflicts.length}{" "}
                        conflicts · {d.requirements.unreadable_pages.length}{" "}
                        source coverage gaps
                      </p>
                    </div>
                  </div>
                  {d.requirements.conflicts.map((c) => (
                    <p className="engineering-warning" key={c.id}>
                      {label(c.key)}: {c.reason}. Correct the chosen value and
                      dismiss superseded evidence.
                    </p>
                  ))}
                  {!!(
                    d.requirements.warnings.length ||
                    d.requirements.unreadable_pages.length
                  ) && (
                    <details className="engineering-source-gaps">
                      <summary>
                        Source details that still need verification
                      </summary>
                      <ul>
                        {[
                          ...d.requirements.unreadable_pages,
                          ...d.requirements.warnings,
                        ].map((entry, index) => {
                          const gap = entry as {
                            reason?: string;
                            detail?: string;
                            page?: number;
                            pages?: number[];
                            source_id?: string;
                            document_id?: string;
                          };
                          const name = d.documents.find(
                            (doc) =>
                              doc.id === (gap.source_id || gap.document_id),
                          )?.name;
                          return (
                            <li key={index}>
                              <strong>
                                {name || "Input coverage"}
                                {gap.page
                                  ? ` · p. ${gap.page}`
                                  : gap.pages?.length
                                    ? ` · p. ${gap.pages.join(", ")}`
                                    : ""}
                              </strong>
                              <p>
                                {gap.reason ||
                                  gap.detail ||
                                  "This source region needs engineering review."}
                              </p>
                            </li>
                          );
                        })}
                      </ul>
                    </details>
                  )}
                  <div className="engineering-table-wrap">
                    <table className="engineering-requirements">
                      <thead>
                        <tr>
                          <th>Confirm</th>
                          <th>Requirement</th>
                          <th>Proposed value</th>
                          <th>Evidence</th>
                          <th>Dismiss</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.requirements.items
                          .filter((i) => !i.dismissed)
                          .map((i) => (
                            <tr key={i.id}>
                              <td>
                                <input
                                  aria-label={`Confirm ${label(i.key)} for ${i.subject_id}`}
                                  type="checkbox"
                                  checked={selected[i.id] ?? i.confirmed}
                                  onChange={(e) =>
                                    setSelected({
                                      ...selected,
                                      [i.id]: e.target.checked,
                                    })
                                  }
                                />
                              </td>
                              <td>
                                <strong>{label(i.key)}</strong>
                                <small>
                                  {i.subject_id === "order"
                                    ? "Order"
                                    : i.subject_id}
                                  {i.excluded ? " · quote exclusion" : ""}
                                  {i.unresolved_role
                                    ? " · device role unresolved"
                                    : ""}
                                  {i.raw_confidence
                                    ? ` · ${i.raw_confidence} reading confidence`
                                    : ""}
                                </small>
                              </td>
                              <td>
                                <input
                                  aria-label={`${label(i.key)} value for ${i.subject_id}`}
                                  value={values[i.id] ?? display(i.value)}
                                  onChange={(e) => {
                                    setValues({
                                      ...values,
                                      [i.id]: e.target.value,
                                    });
                                    setSelected({ ...selected, [i.id]: true });
                                  }}
                                />
                                {i.unit && <small>{i.unit}</small>}
                              </td>
                              <td>
                                {i.source_refs.map((ref, n) => (
                                  <span key={n}>{source(ref)}</span>
                                ))}
                              </td>
                              <td>
                                <input
                                  aria-label={`Dismiss ${label(i.key)} for ${i.subject_id}`}
                                  type="checkbox"
                                  checked={!!dismissed[i.id]}
                                  onChange={(e) =>
                                    setDismissed({
                                      ...dismissed,
                                      [i.id]: e.target.checked,
                                    })
                                  }
                                />
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="engineering-decision">
                    <input
                      aria-label="Requirement review note"
                      placeholder="Review note or reason for a correction"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                    <button
                      disabled={!!busy || reason.trim().length < 3}
                      onClick={() => {
                        const decisions = d
                          .requirements!.items.filter(
                            (i) =>
                              !i.dismissed &&
                              ((selected[i.id] ?? false) || dismissed[i.id]),
                          )
                          .map((i) => {
                            const raw = values[i.id];
                            // The server validates scalar units and explicit
                            // booleans; arbitrary text must not become false.
                            const value = raw === undefined ? i.value : raw;
                            return {
                              id: i.id,
                              value,
                              dismissed: !!dismissed[i.id],
                            };
                          });
                        if (!decisions.length) {
                          setError(
                            "Select the requirements you have checked first.",
                          );
                          return;
                        }
                        void act("requirements", { decisions, reason }).then(
                          (result) => {
                            if (result) {
                              setSelected({});
                              setValues({});
                              setDismissed({});
                            }
                          },
                        );
                      }}
                    >
                      Save reviewed requirements
                    </button>
                    <button
                      className="primary"
                      disabled={!!busy}
                      onClick={() => go("configure")}
                    >
                      Continue to configuration
                    </button>
                  </div>
                </section>
              )}
            </>
          )}
          {stage === "configure" && (
            <>
              <section className="engineering-block">
                <div className="engineering-section-heading">
                  <div>
                    <h2>Proposed lineup</h2>
                    <p>
                      Square D QED-2 · UL 891 scope. Catalog rules constrain
                      proposals; unknowns remain engineer questions.
                    </p>
                  </div>
                  <button
                    className="primary"
                    disabled={!!busy || !d.requirements}
                    onClick={() => void act("configure")}
                  >
                    {busy === "configure"
                      ? "Checking configurations…"
                      : "Configure from requirements"}
                  </button>
                </div>
                {(d.configuration?.configurations || []).map((option) => (
                  <article
                    className={`engineering-option${d.selected_design?.id === option.id ? " selected" : ""}`}
                    key={option.id}
                  >
                    <div className="engineering-section-heading">
                      <div>
                        <h3>
                          {option.sections.length} sections ·{" "}
                          {option.devices.length} devices
                        </h3>
                        <p>
                          {option.sections
                            .map((s) => `${s.width_in ?? "?"} in ${s.kind}`)
                            .join(" + ")}
                        </p>
                      </div>
                      <button
                        disabled={!!busy}
                        onClick={() =>
                          void act("design", { design_id: option.id })
                        }
                      >
                        {d.selected_design?.id === option.id
                          ? "Selected"
                          : "Use this proposal"}
                      </button>
                    </div>
                    <p>
                      {option.fully_configured
                        ? "Known rules satisfied · engineer approval still required"
                        : "Incomplete proposal · resolve the listed questions"}
                    </p>
                    <details>
                      <summary>
                        {
                          option.rule_results.filter((c) => c.status === "pass")
                            .length
                        }{" "}
                        passed checks · {option.unresolved_questions.length}{" "}
                        questions
                      </summary>
                      <RuleChecks checks={option.rule_results} />
                      <ul>
                        {option.unresolved_questions.map((q) => (
                          <li key={q.id}>{q.reason}</li>
                        ))}
                      </ul>
                    </details>
                  </article>
                ))}
                {!d.configuration?.configurations.length && d.configuration && (
                  <ul>
                    {d.configuration.unresolved_questions.map((q) => (
                      <li key={q.id}>{q.reason}</li>
                    ))}
                  </ul>
                )}
              </section>
              {editing && (
                <section className="engineering-block">
                  <h2>Device assignments</h2>
                  <p>
                    Check ratings while editing. Slot changes exchange
                    compatible feeder assignments; their existing mounting
                    details stay attached to each slot. A different catalog
                    variant may require reviewing requirements and configuring
                    again.
                  </p>
                  <div className="engineering-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Device</th>
                          <th>Frame · A</th>
                          <th>Rated current · A</th>
                          <th>Trip · A</th>
                          <th>Section / mounting slot</th>
                        </tr>
                      </thead>
                      <tbody>
                        {editing.devices.map((device) => (
                          <tr key={device.id}>
                            <td>{device.tag || device.id}</td>
                            <td>
                              <input
                                type="number"
                                min="1"
                                aria-label={`Frame amps for ${device.tag || device.id}`}
                                value={device.frame_a ?? ""}
                                onChange={(event) =>
                                  editRating(
                                    device.id,
                                    "frame_a",
                                    event.target.value,
                                  )
                                }
                              />
                              <small>
                                {device.frame || "Catalog frame unknown"}
                              </small>
                            </td>
                            <td>
                              <input
                                type="number"
                                min="1"
                                aria-label={`Rated current amps for ${device.tag || device.id}`}
                                value={device.rated_current_a ?? ""}
                                onChange={(event) =>
                                  editRating(
                                    device.id,
                                    "rated_current_a",
                                    event.target.value,
                                  )
                                }
                              />
                            </td>
                            <td>
                              <input
                                type="number"
                                min="1"
                                aria-label={`Trip amps for ${device.tag || device.id}`}
                                value={device.trip_a ?? ""}
                                onChange={(event) =>
                                  editRating(
                                    device.id,
                                    "trip_a",
                                    event.target.value,
                                  )
                                }
                              />
                            </td>
                            <td>
                              <span>
                                {device.section_id
                                  ? `Section ${editing.sections.findIndex((section) => section.id === device.section_id) + 1}`
                                  : "Unassigned"}
                              </span>
                              <select
                                aria-label={`Exchange mounting slot for ${device.tag || device.id}`}
                                value=""
                                disabled={!!busy || device.role !== "feeder"}
                                onChange={(event) => {
                                  if (event.target.value)
                                    setEditedDesign(
                                      swapEngineeringAssignments(
                                        editing,
                                        device.id,
                                        event.target.value,
                                      ),
                                    );
                                }}
                              >
                                <option value="">
                                  {device.role === "main"
                                    ? "Individual main"
                                    : "Exchange with compatible feeder…"}
                                </option>
                                {editing.devices
                                  .filter((other) =>
                                    engineeringAssignmentPair(
                                      editing,
                                      device.id,
                                      other.id,
                                    ),
                                  )
                                  .map((other) => (
                                    <option value={other.id} key={other.id}>
                                      {other.tag || other.id} · Section{" "}
                                      {editing.sections.findIndex(
                                        (section) =>
                                          section.id === other.section_id,
                                      ) + 1}
                                    </option>
                                  ))}
                              </select>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {editedDesign && (
                    <div
                      className="engineering-live-checks"
                      aria-live="polite"
                      aria-busy={previewBusy}
                    >
                      <h3>Unsaved design checks</h3>
                      <p>
                        {previewBusy
                          ? "Checking current edits…"
                          : currentPreview
                            ? `${currentPreview.rule_results.filter((result) => result.status === "fail").length} failed checks · ${currentPreview.unresolved_questions.length} questions. Saving records one model version.`
                            : "Current edits have not been checked."}
                      </p>
                      {previewError && <ErrorNote message={previewError} />}
                      {currentPreview && !previewBusy && (
                        <>
                          <RuleChecks checks={currentPreview.rule_results} />
                          <ul>
                            {currentPreview.unresolved_questions.map(
                              (question) => (
                                <li key={question.id}>{question.reason}</li>
                              ),
                            )}
                          </ul>
                        </>
                      )}
                    </div>
                  )}
                  <div className="engineering-actions">
                    <button
                      disabled={
                        !!busy ||
                        !editedDesign ||
                        previewBusy ||
                        !currentPreview ||
                        currentPreview.has_known_rule_violations
                      }
                      onClick={() =>
                        void act("design", { design: editedDesign })
                      }
                    >
                      Save checked model edits
                    </button>
                    {editedDesign && (
                      <button
                        disabled={!!busy}
                        onClick={() => {
                          setEditedDesign(null);
                          setPreview(null);
                          setPreviewError("");
                        }}
                      >
                        Discard edits
                      </button>
                    )}
                    <button
                      className="primary"
                      disabled={!!editedDesign || !!busy}
                      onClick={() => go("drawings")}
                    >
                      Continue to drawings
                    </button>
                  </div>
                </section>
              )}
              <details className="engineering-block">
                <summary>Catalog rules and engineer review history</summary>
                <p>
                  Rule edits create an order version. Source citations are
                  required.
                </p>
                <button
                  onClick={() =>
                    void api<{ rule_pack: object }>(
                      `/orders/${id}/engineering/rules`,
                    )
                      .then((r) =>
                        setRulesText(JSON.stringify(r.rule_pack, null, 2)),
                      )
                      .catch((e) => setError(e.message))
                  }
                >
                  Load cited rules
                </button>
                {rulesText && (
                  <>
                    <textarea
                      className="engineering-rule-editor"
                      aria-label="Versioned catalog rule data"
                      value={rulesText}
                      onChange={(e) => setRulesText(e.target.value)}
                    />
                    <input
                      aria-label="Rule change reason"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="Engineer review note"
                    />
                    <button
                      disabled={!!busy || reason.length < 8}
                      onClick={() => {
                        try {
                          void act("rules", {
                            rule_pack: JSON.parse(rulesText),
                            reason,
                          });
                        } catch {
                          setError("Rule data must be valid JSON.");
                        }
                      }}
                    >
                      Save rule version
                    </button>
                  </>
                )}
              </details>
            </>
          )}
          {stage === "drawings" && (
            <>
              <GenerationAssetSettings
                key={`${id}:${d.version}`}
                engineering={d}
                busy={!!busy || !!editedDesign}
                save={(settings) => act("generation-settings", settings)}
                openSource={(documentId) =>
                  void openFile(`/documents/${documentId}/content`).catch(
                    (failure: Error) => setError(failure.message),
                  )
                }
              />
              <section className="engineering-block">
                <div className="engineering-section-heading">
                  <div>
                    <h2>Drawing package</h2>
                    <p>
                      One-line, elevation, BOM, nameplates and submittal PDF
                      from the same model version. Proposed outputs; unknown
                      geometry is labelled unscaled.
                    </p>
                  </div>
                  <button
                    className="primary"
                    disabled={!!busy || !!editedDesign || !d.selected_design}
                    onClick={() => void act("generate")}
                  >
                    {busy === "generate"
                      ? "Generating package…"
                      : "Generate current model"}
                  </button>
                </div>
                {currentPackage ? (
                  <>
                    <ArtifactList
                      artifacts={currentPackage.artifacts}
                      onError={setError}
                    />
                    <p>
                      {currentPackage.unresolved_geometry.length} geometry
                      questions remain. No wiring diagrams or plan views are
                      generated.
                    </p>
                    <div className="engineering-actions">
                      <button
                        disabled={!!busy}
                        onClick={() => void act("precheck")}
                      >
                        Run precheck
                      </button>
                      <button
                        disabled={!!busy}
                        className="primary"
                        onClick={() =>
                          void act(
                            "review",
                            { package_id: currentPackage.id },
                            "review",
                          )
                        }
                      >
                        {currentPackage.document_ids?.length
                          ? "Open existing review"
                          : "Start revision review"}
                      </button>
                    </div>
                  </>
                ) : (
                  <p>
                    {d.selected_design
                      ? "Generate the selected model to see files here."
                      : "Select a proposed configuration first."}
                  </p>
                )}
              </section>
              {d.precheck && (
                <section className="engineering-block">
                  <h2>Precheck · model {d.precheck.model_version}</h2>
                  {d.precheck.saved_drawing_bom_check && (
                    <p>
                      Saved DXF and BOM:{" "}
                      {label(d.precheck.saved_drawing_bom_check.status)}.
                      {d.precheck.saved_drawing_bom_check.status !==
                        "verified" &&
                        " The exported files have not passed the independent consistency check."}
                    </p>
                  )}
                  <RuleChecks checks={d.precheck.validation.rule_results} />
                  <p>{d.precheck.customer_one_line_note}</p>
                  <p>{d.precheck.customer_memory_note}</p>
                  {d.precheck.customer_preferences.map((p, n) => (
                    <p key={n}>{p.title}</p>
                  ))}
                </section>
              )}
            </>
          )}
          {stage === "release" && (
            <section className="engineering-block">
              <h2>Engineer approval and export</h2>
              <p>
                The release stub exports BOM and nameplate data. It does not
                write to an ERP or release production.
              </p>
              {d.approval?.model_version === d.model_version ? (
                <p>
                  <Check size={18} />
                  Approval recorded by {d.approval.signer} ·{" "}
                  {when(d.approval.at)}
                </p>
              ) : (
                <>
                  <p>
                    Current requirements must be confirmed, known rules resolved
                    and precheck completed before recording approval.
                  </p>
                  <div className="engineering-decision">
                    <input
                      aria-label="Engineer signer"
                      placeholder="Engineer’s name"
                      value={signer}
                      onChange={(e) => setSigner(e.target.value)}
                    />
                    <input
                      aria-label="Engineering approval reason"
                      placeholder="Approval record and scope"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                    <button
                      disabled={
                        !!busy || signer.length < 2 || reason.length < 8
                      }
                      onClick={() => void act("approve", { signer, reason })}
                    >
                      Record engineer approval
                    </button>
                  </div>
                </>
              )}
              <button
                disabled={
                  !!busy || d.approval?.model_version !== d.model_version
                }
                onClick={() => void act("release-export")}
              >
                Export approved BOM and nameplates
              </button>
              {d.release_export && (
                <ArtifactList
                  artifacts={(
                    d.packages.find(
                      (p) => p.id === d.release_export!.package_id,
                    )?.artifacts || []
                  ).filter((a) => d.release_export!.files.includes(a.name))}
                  onError={setError}
                />
              )}
            </section>
          )}
          <details className="engineering-history">
            <summary>Order history</summary>
            {d.history.map((h) => (
              <div key={h.version}>
                <strong>{h.summary}</strong>
                <small>
                  Version {h.version} · {h.actor} · {when(h.created_at)}
                </small>
              </div>
            ))}
          </details>
        </main>
      )}
      {upload &&
        (record.data ? (
          <RecordUpload
            w={record.data}
            defaultKind="specification"
            introduction="Add customer specifications, the engineer’s one-line, addenda, quote exceptions or the PO. Choose each source’s role after import."
            close={() => setUpload(false)}
            done={(count) => {
              setUpload(false);
              void qc.invalidateQueries({ queryKey: ["engineering", id] });
              void qc.invalidateQueries({ queryKey: ["record", id] });
              notify(`${count} source files added`);
            }}
          />
        ) : (
          <div className="engineering-error">
            {record.isError ? (
              <ErrorNote message={record.error.message} />
            ) : (
              <Busy text="Opening source upload…" />
            )}
            <button onClick={() => setUpload(false)}>Cancel</button>
          </div>
        ))}
    </section>
  );
}

function MissingRequirementForm({
  engineering,
  busy,
  save,
  openSource,
}: {
  engineering: Engineering;
  busy: boolean;
  save: (requirement: {
    key: string;
    subject_id: string;
    value: string;
    source_id: string;
    page: number;
    reason: string;
  }) => Promise<Engineering | undefined>;
  openSource: (sourceId: string, page?: number) => void;
}) {
  const [field, setField] = useState("");
  const [subject, setSubject] = useState("order");
  const [sourceId, setSourceId] = useState("");
  const [page, setPage] = useState("");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const sources = engineering.documents.filter((document) =>
    [
      "specification",
      "customer_one_line",
      "addendum",
      "quote_exception",
      "purchase_order",
    ].includes(document.role),
  );
  const physicalPage = Number(page);
  const ready =
    !!engineering.requirements &&
    !!field &&
    !!subject.trim() &&
    !!sourceId &&
    Number.isSafeInteger(physicalPage) &&
    physicalPage >= 1 &&
    !!value.trim() &&
    reason.trim().length >= 8;
  return (
    <details className="engineering-block">
      <summary>Add missing requirement</summary>
      <p>
        Add an explicit value you checked on a customer source page. Correct
        existing requirements in the table below; each field and subject can
        have one active requirement.
      </p>
      {!engineering.requirements && (
        <p>Extract current inputs before adding a reviewed requirement.</p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (ready && !busy)
            void save({
              key: field,
              subject_id: subject.trim(),
              value,
              source_id: sourceId,
              page: physicalPage,
              reason,
            });
        }}
      >
        <fieldset
          className="engineering-form-fields"
          disabled={busy || !engineering.requirements}
        >
          <div className="engineering-form-grid">
            <label>
              Missing requirement field
              <select
                required
                value={field}
                onChange={(event) => {
                  setField(event.target.value);
                  if (event.target.value.startsWith("main."))
                    setSubject("order");
                }}
              >
                <option value="">Choose a supported field</option>
                {(engineering.requirement_fields || []).map((key) => (
                  <option key={key} value={key}>
                    {label(key)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Requirement subject
              <input
                required
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
              />
            </label>
            <label>
              Customer source
              <select
                required
                value={sourceId}
                onChange={(event) => setSourceId(event.target.value)}
              >
                <option value="">Choose the source you checked</option>
                {sources.map((document) => (
                  <option key={document.id} value={document.id}>
                    {document.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Physical source page
              <input
                required
                type="number"
                min="1"
                step="1"
                value={page}
                onChange={(event) => setPage(event.target.value)}
              />
            </label>
            <label>
              Source-backed value
              <input
                required
                value={value}
                onChange={(event) => setValue(event.target.value)}
              />
            </label>
            <label>
              Missing requirement review reason
              <input
                required
                minLength={8}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
          </div>
          <p>
            Use order for common and main fields. For a device, use its exact
            printed or reviewed source identity. Supplied fields require an
            explicit true or false value.
          </p>
          <div className="engineering-actions">
            <button
              type="button"
              disabled={!sourceId}
              onClick={() =>
                openSource(
                  sourceId,
                  Number.isSafeInteger(physicalPage) && physicalPage >= 1
                    ? physicalPage
                    : undefined,
                )
              }
            >
              Open cited customer source
            </button>
            <button type="submit" disabled={busy || !ready}>
              Add reviewed requirement
            </button>
          </div>
        </fieldset>
      </form>
    </details>
  );
}

function EquipmentRoleDecision({
  observation,
  documentName,
  busy,
  openSource,
  decide,
}: {
  observation: EquipmentObservation;
  documentName?: string;
  busy: boolean;
  openSource: () => void;
  decide: (role: string, reason: string) => Promise<Engineering | undefined>;
}) {
  const [role, setRole] = useState(
    observation.unresolved_role ? "" : observation.role || "",
  );
  const [reason, setReason] = useState("");
  return (
    <section className="engineering-cut-sheet">
      <div className="engineering-section-heading">
        <h3>
          {observation.display_tag ||
            observation.raw_label ||
            "Unlabelled source object"}
        </h3>
        <button type="button" onClick={openSource}>
          {documentName || "Open source"} · p. {observation.page}
        </button>
      </div>
      <p>
        {observation.unresolved_role
          ? "Equipment role needs review"
          : observation.role === "reference"
            ? "Reference only · excluded from configured equipment"
            : `Engineer-reviewed role: ${label(observation.role || "unknown")}`}
      </p>
      <ul>
        {observation.values.map((value, index) => (
          <li key={`${value.key}:${index}`}>
            {value.key === "rated_current_a"
              ? "Observed current · frame/trip unconfirmed"
              : label(value.key)}
            : {display(value.value)} {value.unit || ""}
          </li>
        ))}
      </ul>
      <div className="engineering-form-grid">
        <label>
          Reviewed equipment role
          <select
            value={role}
            disabled={busy}
            onChange={(event) => setRole(event.target.value)}
          >
            <option value="">Choose after checking the source</option>
            <option value="main">Main</option>
            <option value="feeder">Feeder</option>
            <option value="reference">
              Reference only · exclude from equipment
            </option>
          </select>
        </label>
        <label>
          Equipment role review reason
          <input
            disabled={busy}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Explain this source object's equipment role"
          />
        </label>
      </div>
      <div className="engineering-actions">
        <button
          disabled={busy || !role || reason.trim().length < 8}
          onClick={() => void decide(role, reason)}
        >
          Save this object's reviewed role
        </button>
      </div>
    </section>
  );
}

function GenerationAssetSettings({
  engineering,
  busy,
  save,
  openSource,
}: {
  engineering: Engineering;
  busy: boolean;
  save: (settings: GenerationSettings) => Promise<Engineering | undefined>;
  openSource: (documentId: string) => void;
}) {
  const stored = engineering.generation_settings;
  const [library, setLibrary] = useState(
    stored?.template?.library_document_id || "",
  );
  const [templateId, setTemplateId] = useState(stored?.template?.id || "");
  const [templateVersion, setTemplateVersion] = useState(
    stored?.template?.version || "",
  );
  const [breakerBlock, setBreakerBlock] = useState(
    stored?.template?.blocks?.breaker || "",
  );
  const [titleBlock, setTitleBlock] = useState(
    stored?.template?.blocks?.title_block || "",
  );
  const [elevationMode, setElevationMode] = useState(
    stored?.template?.elevation_mode || "schematic",
  );
  const [mappings, setMappings] = useState(() =>
    (stored?.cut_sheets || []).map((mapping, index) => ({
      ...mapping,
      key: `saved-${index}`,
      pagesText: mapping.pages.join(", "),
    })),
  );
  const [note, setNote] = useState("");
  const [dirty, setDirty] = useState(false);
  const [formError, setFormError] = useState("");
  const libraries = engineering.documents.filter(
    (document) =>
      document.role === "manufacturer_reference" &&
      /\.dxf$/i.test(document.name),
  );
  const cutSheets = engineering.documents.filter(
    (document) =>
      document.role === "component_cut_sheet" && /\.pdf$/i.test(document.name),
  );
  const devices = engineering.selected_design?.devices || [];
  function changeMapping(
    key: string,
    changes: Partial<(typeof mappings)[number]>,
  ) {
    setMappings((current) =>
      current.map((mapping) =>
        mapping.key === key
          ? { ...mapping, verified: false, ...changes }
          : mapping,
      ),
    );
    setDirty(true);
  }
  function changed(setter: (value: string) => void, value: string) {
    setter(value);
    setDirty(true);
  }
  async function saveSettings() {
    setFormError("");
    if (
      library &&
      [templateId, templateVersion, breakerBlock, titleBlock].some(
        (value) => !value.trim(),
      )
    ) {
      setFormError(
        "Enter the reviewed template ID, version and both actual DXF block names.",
      );
      return;
    }
    if (library && elevationMode !== "schematic") {
      setFormError(
        "Choose schematic elevation. Production output is unavailable.",
      );
      return;
    }
    const cut_sheets: CutSheetMapping[] = [];
    for (const mapping of mappings) {
      const pieces = mapping.pagesText
        .trim()
        .split(/[\s,]+/)
        .filter(Boolean);
      const pages = pieces.map(Number);
      if (
        !mapping.document_id ||
        !mapping.component_ids.length ||
        !pages.length ||
        pieces.some((page) => !/^\d+$/.test(page)) ||
        pages.some((page) => !Number.isSafeInteger(page) || page < 1) ||
        new Set(pages).size !== pages.length ||
        !mapping.verified
      ) {
        setFormError(
          "Each cut sheet needs a source, supplied components, unique physical page numbers and an explicit identity check.",
        );
        return;
      }
      const identity = Object.fromEntries(
        Object.entries(mapping.identity)
          .filter(([, value]) => value.trim())
          .map(([key, value]) => [key, value.trim()]),
      );
      if (
        !identity.part_number &&
        !(identity.manufacturer && identity.family && identity.frame)
      ) {
        setFormError(
          "Declare the checked part number, or manufacturer, family and frame shown on the source pages.",
        );
        return;
      }
      cut_sheets.push({
        document_id: mapping.document_id,
        component_ids: mapping.component_ids,
        pages,
        identity,
        verified: true,
      });
    }
    await save({
      template: library
        ? {
            id: templateId.trim(),
            version: templateVersion.trim(),
            library_document_id: library,
            blocks: {
              breaker: breakerBlock.trim(),
              title_block: titleBlock.trim(),
            },
            elevation_mode: elevationMode,
          }
        : null,
      cut_sheets,
      reason: note,
    });
  }
  return (
    <details className="engineering-block engineering-assets">
      <summary>
        Drawing library and supplied-component cut sheets ·{" "}
        {stored?.cut_sheets.length || 0} saved mappings
      </summary>
      <p>
        Select classified order documents and review their exact identity before
        generating. Source roles are managed in Requirements.
      </p>
      <fieldset disabled={busy}>
        <legend>Drawing blocks</legend>
        <div className="engineering-form-grid">
          <label>
            DXF library
            <select
              value={library}
              onChange={(event) => changed(setLibrary, event.target.value)}
            >
              <option value="">Built-in schematic blocks</option>
              {libraries.map((document) => (
                <option key={document.id} value={document.id}>
                  {document.name}
                </option>
              ))}
            </select>
          </label>
          {library && (
            <>
              <label>
                Reviewed template ID
                <input
                  value={templateId}
                  onChange={(event) =>
                    changed(setTemplateId, event.target.value)
                  }
                />
              </label>
              <label>
                Template version
                <input
                  value={templateVersion}
                  onChange={(event) =>
                    changed(setTemplateVersion, event.target.value)
                  }
                />
              </label>
              <label>
                Breaker block name
                <input
                  value={breakerBlock}
                  onChange={(event) =>
                    changed(setBreakerBlock, event.target.value)
                  }
                />
              </label>
              <label>
                Title block name
                <input
                  value={titleBlock}
                  onChange={(event) =>
                    changed(setTitleBlock, event.target.value)
                  }
                />
              </label>
              <label>
                Elevation mode
                <select
                  value={elevationMode}
                  onChange={(event) =>
                    changed(setElevationMode, event.target.value)
                  }
                >
                  <option value="schematic">
                    Schematic · unknown dimensions labelled
                  </option>
                  <option value="production" disabled>
                    Production unavailable
                  </option>
                </select>
              </label>
            </>
          )}
        </div>
        {library && (
          <button type="button" onClick={() => openSource(library)}>
            Open selected DXF source
          </button>
        )}
      </fieldset>
      <fieldset disabled={busy}>
        <legend>Component cut sheets</legend>
        {!devices.some((device) => device.supplied === true) && (
          <p>
            No components are confirmed as supplied. Review component supply
            before mapping cut sheets.
          </p>
        )}
        {mappings.map((mapping, index) => (
          <section className="engineering-cut-sheet" key={mapping.key}>
            <div className="engineering-section-heading">
              <h3>Mapping {index + 1}</h3>
              <button
                type="button"
                onClick={() => {
                  setMappings(
                    mappings.filter((item) => item.key !== mapping.key),
                  );
                  setDirty(true);
                }}
              >
                Remove mapping
              </button>
            </div>
            <div className="engineering-form-grid">
              <label>
                Source PDF
                <select
                  value={mapping.document_id}
                  onChange={(event) =>
                    changeMapping(mapping.key, {
                      document_id: event.target.value,
                    })
                  }
                >
                  <option value="">Choose a classified cut sheet</option>
                  {cutSheets.map((document) => (
                    <option value={document.id} key={document.id}>
                      {document.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Physical PDF pages
                <input
                  placeholder="For example: 1, 3, 4"
                  value={mapping.pagesText}
                  onChange={(event) =>
                    changeMapping(mapping.key, {
                      pagesText: event.target.value,
                    })
                  }
                />
              </label>
            </div>
            {mapping.document_id && (
              <button
                type="button"
                onClick={() => openSource(mapping.document_id)}
              >
                Open source pages
              </button>
            )}
            <div
              className="engineering-component-options"
              role="group"
              aria-label={`Supplied components for mapping ${index + 1}`}
            >
              {devices
                .filter(
                  (device) =>
                    device.supplied === true ||
                    mapping.component_ids.includes(device.id),
                )
                .map((device) => (
                  <label key={device.id}>
                    <input
                      type="checkbox"
                      checked={mapping.component_ids.includes(device.id)}
                      disabled={device.supplied !== true}
                      onChange={(event) =>
                        changeMapping(mapping.key, {
                          component_ids: event.target.checked
                            ? [...mapping.component_ids, device.id]
                            : mapping.component_ids.filter(
                                (component) => component !== device.id,
                              ),
                        })
                      }
                    />
                    <span>
                      {device.tag || device.id}
                      <small>
                        {[
                          device.manufacturer,
                          device.family,
                          device.frame,
                          device.part_number,
                        ]
                          .filter(Boolean)
                          .join(" · ") || "Identity unknown"}
                      </small>
                    </span>
                  </label>
                ))}
            </div>
            <div className="engineering-form-grid">
              {["manufacturer", "family", "frame", "part_number"].map(
                (field) => (
                  <label key={field}>
                    Source {label(field)}
                    <input
                      value={mapping.identity[field] || ""}
                      onChange={(event) =>
                        changeMapping(mapping.key, {
                          identity: {
                            ...mapping.identity,
                            [field]: event.target.value,
                          },
                        })
                      }
                    />
                  </label>
                ),
              )}
            </div>
            <label className="engineering-identity-check">
              <input
                type="checkbox"
                checked={mapping.verified}
                onChange={(event) =>
                  changeMapping(mapping.key, { verified: event.target.checked })
                }
              />
              I checked these PDF pages against the selected supplied
              components.
            </label>
          </section>
        ))}
        <button
          type="button"
          disabled={
            !engineering.selected_design ||
            !cutSheets.length ||
            !devices.some((device) => device.supplied === true)
          }
          onClick={() => {
            setMappings([
              ...mappings,
              {
                key: crypto.randomUUID(),
                document_id: "",
                component_ids: [],
                pages: [],
                pagesText: "",
                identity: {},
                verified: false,
              },
            ]);
            setDirty(true);
          }}
        >
          Add cut-sheet mapping
        </button>
        {!cutSheets.length && (
          <p>
            Classify an uploaded PDF as a Component cut sheet in Requirements to
            add a mapping.
          </p>
        )}
      </fieldset>
      <label className="engineering-review-note">
        Asset review note
        <input
          value={note}
          placeholder="Record the identity and drawing-library review"
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      {formError && <ErrorNote message={formError} />}
      <div className="engineering-actions">
        <button
          disabled={busy || !dirty || note.trim().length < 8}
          onClick={() => void saveSettings()}
        >
          Save reviewed generation settings
        </button>
      </div>
    </details>
  );
}

function ArtifactList({
  artifacts,
  onError,
}: {
  artifacts: Artifact[];
  onError: (message: string) => void;
}) {
  return (
    <div className="engineering-artifacts">
      {artifacts.map((a) => (
        <button
          key={a.name}
          onClick={() =>
            void openFile(a.url, a.name).catch((e) => onError(e.message))
          }
        >
          <DownloadSimple size={18} />
          <span>
            <strong>{a.name}</strong>
            <small>{a.kind}</small>
          </span>
        </button>
      ))}
    </div>
  );
}
function RuleChecks({ checks }: { checks: CheckResult[] }) {
  return (
    <ul className="engineering-checks">
      {checks.map((c, n) => (
        <li key={`${c.rule_id}:${n}`}>
          <span className={`engineering-check-status ${c.status}`}>
            {c.status.replaceAll("_", " ")}
          </span>
          <div>
            <strong>{label(c.rule_id)}</strong>
            <p>{c.reason}</p>
            {c.source_refs.map((ref, i) => (
              <a key={i} href={ref.url} target="_blank" rel="noreferrer">
                Catalog p. {ref.page}{" "}
              </a>
            ))}
          </div>
        </li>
      ))}
    </ul>
  );
}
