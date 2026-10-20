export type EquipmentRole = "drawing" | "bom" | "specification" | "markup";
export type EquipmentDocument = {
  id: string;
  name: string;
  role: EquipmentRole | string;
  revision: string;
  sha256?: string;
  state?: string;
};
export type EquipmentEvidence = {
  document_id: string;
  name: string;
  role?: string;
  revision?: string;
  page?: number | null;
  sheet?: string | null;
  row?: number | string | null;
  cell?: string | null;
  cad_handle?: string | null;
  bbox?: number[] | null;
  excerpt?: string;
  url?: string;
  crop_url?: string;
};
export type EquipmentCell = {
  value: unknown;
  evidence: EquipmentEvidence[];
};
export type EquipmentCheck = {
  id: string;
  device_tag: string;
  field: string;
  label: string;
  baseline_drawing: EquipmentCell;
  target_drawing: EquipmentCell;
  target_bom: EquipmentCell;
  required: EquipmentCell;
  status: string;
  reason: string;
};
export type EquipmentComparison = {
  baseline_revision: string;
  target_revision: string;
  summary: {
    verified: number;
    remaining: number;
    cannot_verify: number;
    unchanged?: number;
    total?: number;
    source_issues?: number;
    outcome?: string;
    revision_changes_verified?: boolean;
  };
  checks: EquipmentCheck[];
  checked_at?: string;
  fingerprint?: string;
  stale?: boolean;
  coverage?: Record<string, unknown> | null;
  warnings?: string[];
  model?: { tag: string; fields?: Record<string, string[]> }[];
};
export type EquipmentView = {
  order_id: string;
  version: number;
  available_documents: EquipmentDocument[];
  packages: { revision: string; documents: EquipmentDocument[] }[];
  comparison: EquipmentComparison | null;
  limitations: string[];
};
export type EquipmentStatus =
  "verified" | "unchanged" | "remaining" | "cannot_verify";

/** Only an explicit, current result can become a verified check. */
export function equipmentStatus(
  status: string,
  stale = false,
): EquipmentStatus {
  if (stale) return "cannot_verify";
  if (status === "verified") return "verified";
  if (status === "unchanged") return "unchanged";
  if (
    [
      "remaining",
      "mismatch",
      "missing",
      "conflict",
      "not_implemented",
      "failed",
      "partial",
    ].includes(status)
  )
    return "remaining";
  return "cannot_verify";
}
export function equipmentCounts(comparison?: EquipmentComparison | null) {
  const counts = { verified: 0, remaining: 0, cannot_verify: 0, unchanged: 0 };
  for (const check of comparison?.checks || [])
    counts[equipmentStatus(check.status, comparison?.stale)]++;
  return counts;
}
export function equipmentVerdict(comparison: EquipmentComparison): string {
  const counts = equipmentCounts(comparison);
  if (comparison.stale) return "Re-check the updated files";
  if (counts.remaining || comparison.summary.outcome === "changes_remaining")
    return "Changes remain in the revised files";
  if (
    counts.cannot_verify ||
    comparison.summary.source_issues ||
    comparison.summary.outcome === "cannot_verify"
  )
    return "Some changes cannot be verified";
  if (counts.verified) return "Supported equipment fields match";
  if (counts.unchanged) return "The observed fields are unchanged";
  return "No supported equipment fields found";
}
export function equipmentChecks(
  comparison: EquipmentComparison,
): EquipmentCheck[] {
  const priority = {
    remaining: 0,
    cannot_verify: 1,
    verified: 2,
    unchanged: 3,
  };
  return [...comparison.checks].sort(
    (a, b) =>
      priority[equipmentStatus(a.status, comparison.stale)] -
        priority[equipmentStatus(b.status, comparison.stale)] ||
      a.device_tag.localeCompare(b.device_tag) ||
      a.field.localeCompare(b.field),
  );
}
export function equipmentSources(
  cell: EquipmentCell,
  field: string,
): EquipmentEvidence[] {
  const evidence = cell?.evidence || [];
  // Presence uses all observed fields for one device. One link per original
  // proves its presence without repeating that original for every attribute.
  return field === "device_presence"
    ? [
        ...new Map(
          evidence.map((source) => [source.document_id, source]),
        ).values(),
      ]
    : evidence;
}
export function equipmentValue(value: unknown, field = ""): string {
  if (field === "device_presence")
    return value === null || value === undefined || value === ""
      ? "Not located"
      : "Present";
  if (value === null || value === undefined || value === "") return "Not found";
  if (Array.isArray(value))
    return value.length
      ? value.map((v) => equipmentValue(v, field)).join(" · ")
      : "Not found";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  const unit = (
    {
      frame_a: "A",
      trip_a: "A",
      control_voltage_v: "V",
      interrupting_ka: "kA",
    } as Record<string, string>
  )[field];
  return unit && /^-?\d+(?:\.\d+)?(?:\s*\/\s*-?\d+(?:\.\d+)?)*$/.test(text)
    ? `${text} ${unit}`
    : text;
}
export function equipmentLocation(evidence: EquipmentEvidence): string {
  return [
    evidence.sheet && `Sheet ${evidence.sheet}`,
    evidence.page && `p. ${evidence.page}`,
    evidence.row && `row ${evidence.row}`,
    evidence.cell && `cell ${evidence.cell}`,
    evidence.cad_handle && `CAD element ${evidence.cad_handle}`,
  ]
    .filter(Boolean)
    .join(" · ");
}
export function equipmentRevisions(view?: EquipmentView): string[] {
  return [
    ...new Set(view?.packages.map((p) => p.revision).filter(Boolean) || []),
  ];
}
export function equipmentPair(view: EquipmentView): [string, string] {
  const revisions = equipmentRevisions(view);
  if (
    view.comparison &&
    revisions.includes(view.comparison.baseline_revision) &&
    revisions.includes(view.comparison.target_revision)
  )
    return [view.comparison.baseline_revision, view.comparison.target_revision];
  return [revisions.at(-2) || "", revisions.at(-1) || ""];
}
export function equipmentRole(role: string): string {
  return (
    (
      {
        drawing: "Drawing",
        bom: "BOM",
        specification: "Requirement",
        markup: "Returned markup",
      } as Record<string, string>
    )[role] || "Document"
  );
}
export function equipmentEvidencePath(
  orderId: string,
  evidence: EquipmentEvidence,
): string | null {
  if (!evidence.document_id) return null;
  const allowed = `/api/orders/${encodeURIComponent(orderId)}/equipment/evidence?`;
  const crop = evidence.crop_url?.startsWith("/orders/")
    ? `/api${evidence.crop_url}`
    : evidence.crop_url;
  if (crop?.startsWith(allowed)) {
    const query = new URLSearchParams(crop.slice(allowed.length));
    if (query.get("document_id") === evidence.document_id) return crop;
  }
  if (!evidence.page) return null;
  const query = new URLSearchParams({
    document_id: evidence.document_id,
    page: String(evidence.page),
  });
  return `/api/orders/${encodeURIComponent(orderId)}/equipment/evidence?${query}`;
}
