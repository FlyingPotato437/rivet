export type RecordAgentOperation = {
  type: "link_source" | "draft_clarification" | "create_task" | "draft_summary";
  comment_id?: string;
  target_source_id?: string;
  text?: string;
  role?: string;
  title?: string;
  evidence_ids: string[];
};
export type RecordAgentProposal = {
  id: string;
  run_id: string;
  order_id: string;
  kind: string;
  title: string;
  summary: string;
  status: "pending" | "accepted" | "rejected" | "stale";
  origin: "ai" | "rules";
  operations: RecordAgentOperation[];
  before: unknown;
  after: unknown;
  evidence_ids: string[];
  base_version: number;
  created_at: string;
  resolution?: unknown;
};
export type RecordAgentRun = {
  id: string;
  order_id: string;
  goal: string;
  status:
    | "queued"
    | "running"
    | "waiting_for_review"
    | "waiting_for_input"
    | "completed"
    | "failed"
    | "cancelled";
  mode: "ai" | "rules";
  origin: "user" | "event";
  base_version: number;
  steps: {
    id: string;
    name: string;
    label: string;
    status: "completed" | "failed";
    summary: string;
    evidence_ids: string[];
    at: string;
    usage?: Record<string, unknown>;
  }[];
  events: { id: string; kind: string; message: string; at: string }[];
  result: {
    summary?: string;
    question?: string;
    error?: string;
    coverage?: unknown;
  };
  usage: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    model_calls: number;
  };
  budget: {
    max_steps: number;
    max_output_tokens: number;
    max_total_tokens: number;
  };
  model: string;
  created_at: string;
  updated_at: string;
  proposals: RecordAgentProposal[];
};
export type RecordAgentSettings = {
  enabled: boolean;
  max_steps: number;
  max_output_tokens: number;
  playbook: string;
  version: number;
};
export type RecordAgentView = {
  runs: RecordAgentRun[];
  proposals: RecordAgentProposal[];
  settings: RecordAgentSettings;
  graph_summary: { nodes: number; edges: number };
};
export type RecordAgentAnswer = {
  answer: string;
  source_ids: string[];
  comment_ids: string[];
  change_ids: string[];
  version: number;
  coverage?: unknown;
};
export type RecordConversation = {
  order_id: string;
  messages: {
    id: string;
    question: string;
    result: RecordAgentAnswer;
    created_at: string;
  }[];
};
