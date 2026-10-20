import type { EquipmentComparison } from "./equipment-types";
import { equipmentValue } from "./equipment-types.ts";
import {
  nativeChecksPass,
  type NativeVerification,
} from "./native-drawing-types.ts";

export type CorrectionIssue =
  string | { reason?: string; message?: string; title?: string; code?: string };
export type EquipmentEdit = {
  kind?: string;
  device_tag: string;
  field: string;
  before?: unknown;
  after?: unknown;
  label?: string;
  document_id?: string;
  role?: string;
};
export type CorrectedArtifact = {
  name: string;
  role?: string;
  sha256: string;
  media_type?: string;
  url: string;
};
export type CompanyRuleCheck = {
  active: boolean;
  status: "pass" | "fail" | "unknown" | "inactive";
  profile_version: string;
  blockers: string[];
  results: {
    rule_id: string;
    label: string;
    status: "pass" | "fail" | "unknown";
    applicable: boolean | null;
    reason: string;
    rule_sources: { name: string; location: { page?: number } }[];
  }[];
};
export type CorrectedBundle = {
  id: string;
  status: string;
  target_revision: string;
  created_at: string;
  attachable: boolean;
  current?: boolean;
  changes: EquipmentEdit[];
  verification: EquipmentComparison;
  artifacts: CorrectedArtifact[];
  native_verification?: NativeVerification;
  company_rules?: CompanyRuleCheck;
  outlook?: {
    draft?: {
      id?: string;
      status: string;
      web_link?: string;
      outlook_url?: string;
      error?: string;
      attachments?: { name: string; sha256?: string }[];
    };
    status?: string;
    error?: string;
  };
};
export type EquipmentCorrectionsView = {
  order_id: string;
  version: number;
  automation?: { status: string; error?: string; detail?: string };
  plan: {
    plan_id: string;
    status: string;
    edits: EquipmentEdit[];
    blockers: CorrectionIssue[];
    warnings?: CorrectionIssue[];
    summary?: unknown;
    native_plan?: NativeVerification;
  };
  bundles: CorrectedBundle[];
};
export function correctionIssue(issue: CorrectionIssue) {
  return typeof issue === "string"
    ? issue
    : issue.reason ||
        issue.message ||
        issue.title ||
        "This point needs an engineer before Rivet can prepare the files.";
}
export function correctionBefore(edit: EquipmentEdit) {
  if (
    edit.before &&
    typeof edit.before === "object" &&
    !Array.isArray(edit.before)
  ) {
    const values = edit.before as { drawing?: unknown; bom?: unknown };
    if ("drawing" in values || "bom" in values) {
      const drawing = equipmentValue(values.drawing, edit.field),
        bom = equipmentValue(values.bom, edit.field);
      return drawing === bom ? drawing : `Drawing: ${drawing} · BOM: ${bom}`;
    }
  }
  return equipmentValue(edit.before, edit.field);
}
export function literalCorrectionEdits(view: EquipmentCorrectionsView) {
  const nativeKinds = new Set([
    "move_device",
    "resize_device",
    "add_device",
    "remove_device",
    "rewire",
    "reroute",
    "add_wire",
    "remove_wire",
  ]);
  return view.plan.edits.filter(
    (edit) => !view.plan.native_plan || !nativeKinds.has(edit.kind || ""),
  );
}
export function correctedBundleReady(bundle: CorrectedBundle) {
  const summary = bundle.verification?.summary;
  return (
    bundle.status === "prepared_draft" &&
    bundle.current !== false &&
    bundle.attachable === true &&
    !!summary &&
    summary.remaining === 0 &&
    summary.cannot_verify === 0 &&
    !summary.source_issues &&
    summary.outcome === "supported_fields_match" &&
    companyRulesAllowAttachment(bundle.company_rules) &&
    (!bundle.native_verification ||
      nativeChecksPass(bundle.native_verification))
  );
}
export function companyRulesAllowAttachment(rules?: CompanyRuleCheck) {
  if (!rules) return true;
  return (
    rules.active === true &&
    Array.isArray(rules.blockers) &&
    rules.blockers.length === 0 &&
    Array.isArray(rules.results) &&
    rules.results.every(
      (result) => result.applicable === false || result.status === "pass",
    )
  );
}
export function correctedDraftReady(bundle: CorrectedBundle) {
  const expected = correctedFiles(bundle),
    actual = bundle.outlook?.draft?.attachments || [];
  return (
    correctedBundleReady(bundle) &&
    bundle.outlook?.draft?.status === "created" &&
    !bundle.outlook.draft.error &&
    expected.length > 0 &&
    expected.every((file) =>
      actual.some(
        (saved) =>
          saved.name === file.name &&
          (!saved.sha256 || saved.sha256 === file.sha256),
      ),
    )
  );
}
export function correctedFiles(bundle: CorrectedBundle) {
  return bundle.artifacts.filter(
    (file) =>
      file.role !== "manifest" && /\.(pdf|dxf|xlsx|csv)$/i.test(file.name),
  );
}
export function correctedChecks(bundle: CorrectedBundle) {
  return (bundle.verification.checks || []).filter((check) =>
    bundle.changes.some(
      (change) =>
        change.device_tag === check.device_tag && change.field === check.field,
    ),
  );
}
export function newestCorrectedBundle(view?: EquipmentCorrectionsView) {
  return [...(view?.bundles || [])]
    .filter((bundle) => bundle.current !== false)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
}
export function correctionArtifactPath(
  orderId: string,
  bundleId: string,
  artifact: CorrectedArtifact,
) {
  const prefix = `/orders/${encodeURIComponent(orderId)}/equipment/corrections/${encodeURIComponent(bundleId)}/files/`;
  const raw = artifact.url?.startsWith("/api/")
    ? artifact.url.slice(4)
    : artifact.url;
  return raw?.startsWith(prefix) && !raw.includes("?") && !raw.includes("#")
    ? `/api${raw}`
    : null;
}
