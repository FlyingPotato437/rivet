export type ClarificationStatus =
  "draft" | "awaiting_reply" | "answered" | "resolved";

export type Clarification = {
  id: string;
  project_id: string;
  title: string;
  question: string;
  recipient: string;
  due_date: string | null;
  line_ids: string[];
  source_ids: string[];
  run_id: string | null;
  last_run_id?: string | null;
  status: ClarificationStatus;
  answer: string;
  answer_source_ids: string[];
  answer_origin?: string | null;
  resolution_note?: string | null;
  created_at: string;
  updated_at: string;
  revision: number;
};

export type WorkQueueStatus =
  "attention" | "waiting" | "review" | "processing" | "complete";
export type WorkQueueItem = {
  id: string;
  project_id: string;
  project_title: string;
  customer: string;
  quote_id: string;
  quote_number: string;
  kind: "clarification" | "proposal" | "run" | "document" | "review" | "setup";
  status: WorkQueueStatus;
  title: string;
  detail: string;
  updated_at: string;
  due_date: string | null;
  target: "coordination" | "changes" | "sources" | "quote";
  clarification_id?: string;
};
export type WorkQueueView = {
  items: WorkQueueItem[];
  counts: Record<WorkQueueStatus, number>;
};
