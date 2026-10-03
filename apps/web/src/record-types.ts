import type { OrderDocument, OrderSource } from "./order-types";
export type RecordComment = {
  id: string;
  number: string;
  revision: string;
  text: string;
  original_text: string;
  author: string;
  authored_at: string;
  document_id: string;
  source_ids: string[];
  source_page: number | null;
  target_source_id: string;
  linked_email_id: string;
  status: "open" | "responded" | "closed";
  response: string;
  responder: string;
  reviewed: boolean;
  confidence: string;
  flags: string[];
  created_at: string;
  updated_at: string;
  origin: string;
};
export type RecordChange = {
  id: string;
  title: string;
  before: string;
  after: string;
  from_revision: string;
  to_revision: string;
  comment_ids: string[];
  requested_by: string;
  approved_by: string;
  approved_at: string;
  created_at: string;
};
export type RecordDocument = OrderDocument & {
  email?: { subject: string; from: string; date: string };
  created_at: string;
};
export type RecordOrder = {
  id: string;
  project_id: string;
  title: string;
  customer: string;
  number: string;
  category: string;
  synthetic: boolean;
};
export type RecordApproval = {
  id: string;
  label: string;
  actor: string;
  reason: string;
  at: string;
  version: number;
};
export type RecordView = {
  connections: {
    comments: Record<
      string,
      {
        source_ids: string[];
        document_id: string;
        drawing_source_id: string;
        email_document_id: string;
        change_ids: string[];
        event_ids: string[];
        in_approved_record: boolean;
        matches_approved_record: boolean;
      }
    >;
    latest_approval_id: string;
    after_approval: {
      comment_ids: string[];
      change_ids: string[];
      document_ids: string[];
    } | null;
  };
  order: RecordOrder;
  version: number;
  comments: RecordComment[];
  changes: RecordChange[];
  documents: RecordDocument[];
  sources: OrderSource[];
  events: {
    id: string;
    kind: string;
    summary: string;
    actor: string;
    at: string;
    reason: string;
    before: unknown;
    after: unknown;
    item_id: string;
  }[];
  subscribers: { id: string; name: string; email: string; team: string }[];
  notices: {
    id: string;
    title: string;
    body: string;
    recipients: { name: string; email: string; team: string }[];
    at: string;
    status: string;
  }[];
  approvals: RecordApproval[];
  shares: {
    id: string;
    label: string;
    revoked: boolean;
    created_at: string;
    expires_at: string;
  }[];
};
export type RecordSummary = RecordOrder & {
  version: number;
  updated_at: string;
  revision: string;
  counts: {
    comments: number;
    open: number;
    responded: number;
    closed: number;
    review: number;
    notices: number;
  };
  approval: RecordApproval | null;
};
