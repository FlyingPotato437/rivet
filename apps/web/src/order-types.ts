export type OrderValue =
  string | number | boolean | null | Record<string, unknown> | unknown[];
export type CheckStatus = "pass" | "fail" | "unknown" | "conflict" | "waived";
export type OrderSource = {
  id: string;
  document_id: string;
  text: string;
  location: {
    page?: number;
    bbox?: number[];
    cells?: string;
    line?: number;
    [key: string]: unknown;
  };
};
export type OrderDocument = {
  public_source?: { url: string; pages: number[]; sha256: string };
  id: string;
  name: string;
  kind: string;
  state: string;
  role: string;
  revision_label: string | null;
  coverage: Array<{ label?: string; state?: string; detail?: string }>;
  warnings: string[];
};
export type OrderSummary = {
  id: string;
  project_id: string;
  title: string;
  customer: string;
  number: string;
  category: string;
  version: number;
  revision_label: string;
  status: string;
  updated_at: string;
  synthetic: boolean;
  counts: {
    checks: number;
    passing: number;
    failing: number;
    unknown: number;
    waived: number;
    decisions: number;
    tasks: number;
  };
  snapshot_hash: string;
};
export type OrderCheck = {
  id: string;
  fingerprint: string;
  device: string;
  attribute: string;
  title: string;
  status: CheckStatus;
  expected: OrderValue;
  actual: OrderValue;
  unit: string;
  detail: string;
  source_ids: string[];
  actual_source_ids: string[];
  waiver?: { signer: string; reason: string; at?: string };
};
export type Obligation = {
  id: string;
  device: string;
  attribute: string;
  label: string;
  expected: OrderValue;
  actual: OrderValue;
  unit: string;
  status: CheckStatus;
  source_ids: string[];
  actual_source_ids: string[];
  precedence: string;
  conflict_values: OrderValue[];
};
export type OrderAction = {
  id: string;
  type: "configuration" | "response" | "rfi" | "task" | "alias";
  title: string;
  summary: string;
  status: "pending" | "accepted" | "rejected" | "stale";
  base_version: number;
  before: OrderValue;
  after: OrderValue;
  source_ids: string[];
  reason: string;
  created_at: string;
};
export type PropagationTask = {
  id: string;
  title: string;
  target: "bom" | "supplier_po" | "nameplate" | "drawing";
  device: string;
  attribute: string;
  expected: OrderValue;
  actual: OrderValue;
  status: "pending" | "verified";
  source_ids: string[];
  actual_source_ids: string[];
};
export type OrderComment = {
  id: string;
  number: string | number;
  text: string;
  device: string;
  attribute: string;
  source_ids: string[];
  status: string;
  response: string;
};
export type OrderHistory = {
  version: number;
  revision_label: string;
  summary: string;
  actor: string;
  at: string;
  checksum: string;
  is_processing?: boolean;
};
export type OrderWorkspaceView = {
  order: OrderSummary;
  documents: OrderDocument[];
  sources: OrderSource[];
  ledger: Obligation[];
  checks: OrderCheck[];
  actions: OrderAction[];
  tasks: PropagationTask[];
  comments: OrderComment[];
  history: OrderHistory[];
  drafts?: Array<{
    id: string;
    type: string;
    title: string;
    text: string;
    source_ids: string[];
    status: string;
  }>;
  events: Array<{
    id: string;
    kind: string;
    summary: string;
    actor: string;
    at: string;
  }>;
  release: {
    ready: boolean;
    blockers: string[];
    approval: null | {
      signer: string;
      reason: string;
      version: number;
      snapshot_hash: string;
      at: string;
    };
  };
  capabilities: {
    ai_configured: boolean;
    inbox_connected: boolean;
    cad_supported: boolean;
  };
};
export type OrderCommandResult = {
  answer: string;
  source_ids: string[];
  actions: OrderAction[];
  workspace: OrderWorkspaceView;
};
export type RevisionDiff = {
  from_version: number;
  to_version: number;
  changes: Array<{
    device: string;
    attribute: string;
    before: OrderValue;
    after: OrderValue;
    unit: string;
    source_ids: string[];
    before_source_ids?: string[];
    expected_before?: OrderValue;
    expected_after?: OrderValue;
    status_before?: string;
    status_after?: string;
  }>;
  checks_before: OrderCheck[];
  checks_after: OrderCheck[];
};
