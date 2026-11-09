export type RedesignCommentStatus =
  "changed" | "already_present" | "needs_information" | "not_applicable";

export type RedesignRunStatus =
  | "queued"
  | "running"
  | "needs_information"
  | "ready_for_review"
  | "approved"
  | "changes_requested"
  | "failed";

export type DrawingRedesignComment = {
  id: string;
  text: string;
  source_document_id: string;
  source_name?: string;
  page: number | null;
  source_id?: string;
  source_location?: {
    page: number;
    bbox: number[];
    text_bbox?: number[];
    annotation_id: string;
    coordinate_system: "visible-page-v1";
    extraction_method: "native_pdf_annotation" | "vision";
    transcription_confidence: "high" | "medium" | "low";
    approximate: boolean;
    original_sha256: string;
  };
  grounding_status?: "exact" | "approximate" | "unavailable";
  status: RedesignCommentStatus;
  explanation: string;
  change_ids: string[];
};

export type DrawingRedesignChange = {
  id: string;
  comment_id: string;
  label: string;
  before: string;
  after: string;
  kind: "cad_text" | "geometry" | "spec_text";
  verified: boolean;
  source_locator: string;
  comparison: { before_url: string; after_url: string };
  review?: {
    decision: "accepted" | "needs_changes";
    note: string;
    actor: string;
    at: string;
  };
};

export type DrawingRedesignArtifact = {
  name: string;
  url: string;
  role: string;
  media_type: string;
  bytes: number;
  sha256: string;
};

export type DrawingRedesignRun = {
  id: string;
  status: RedesignRunStatus;
  current: boolean;
  summary: string;
  totals: {
    comments: number;
    changed: number;
    already_present: number;
    needs_information: number;
  };
  comments: DrawingRedesignComment[];
  changes: DrawingRedesignChange[];
  artifacts: DrawingRedesignArtifact[];
  approval_eligible: boolean;
  remaining_issues: string[];
  error?: string;
  native_source_id?: string;
  revised_from_run_id?: string;
  review_eligible?: boolean;
  implementation_current?: boolean;
  can_reprepare?: boolean;
  visual_findings?: DrawingRedesignComment[];
  revision_checks?: Array<{
    change_id: string;
    comment_id: string;
    label: string;
    expected: string;
    status: "verified" | "needs_review";
    evidence: unknown[];
  }>;
};

export type DrawingRedesignView = {
  order_id: string;
  order_version: number;
  source_ready: boolean;
  latest: DrawingRedesignRun | null;
};

export function redesignIsActive(status: RedesignRunStatus): boolean {
  return status === "queued" || status === "running";
}

export function redesignCanApprove(run: DrawingRedesignRun): boolean {
  return (
    run.current &&
    run.status === "ready_for_review" &&
    run.approval_eligible &&
    run.remaining_issues.length === 0
  );
}

export function redesignStatusLabel(status: RedesignRunStatus): string {
  return {
    queued: "Queued",
    running: "In progress",
    needs_information: "Needs information",
    ready_for_review: "Ready for engineer review",
    approved: "Approved",
    changes_requested: "Changes requested",
    failed: "Could not complete",
  }[status];
}

/** Keep authenticated media and downloads on the Rivet API origin. */
export function redesignApiPath(url: string): string {
  const uuid =
    "[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
  const match = new RegExp(
    `^/api/orders/(${uuid})/engineering/redesign/(${uuid})/files/([^/?#]+)$`,
    "i",
  ).exec(url);
  const invalid = () => {
    throw new Error("This file link is not available.");
  };
  if (!match) return invalid();
  const filename = (() => {
    try {
      return decodeURIComponent(match[3]);
    } catch {
      return "";
    }
  })();
  if (
    !filename ||
    filename === "." ||
    filename === ".." ||
    filename.includes("..") ||
    /[\\/?#%\u0000-\u001f]/.test(filename)
  )
    return invalid();
  return url;
}
