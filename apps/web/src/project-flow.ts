export type ProjectStatus =
  | "handling"
  | "ready"
  | "prepared"
  | "waiting_customer"
  | "needs_you"
  | "watching"
  | "up_to_date";
export type ProjectCommunication = {
  thread_key?: string;
  id: string;
  direction: "received" | "sent";
  subject: string;
  sender: string;
  at: string;
  body: string;
  outlook_url: string;
  origin: string;
  attachments?: { name: string; filename?: string }[];
};
export type ProjectFlow = {
  equipment_correction?:
    import("./equipment-correction-types").CorrectedBundle | null;
  threads?: {
    id: string;
    subject: string;
    status: ProjectStatus;
    status_label: string;
    last_at: string;
    email_count: number;
  }[];
  reply_preparation?: import("./OrderPreparedReply").Preparation | null;
  id: string;
  project_id: string;
  name: string;
  name_version: number;
  number: string;
  customer: string;
  sample: boolean;
  status: ProjectStatus;
  status_label: string;
  explanation: string;
  last_communication: ProjectCommunication | null;
  last_sent_at: string | null;
  last_received_at: string | null;
  email_count: number;
  thread_count: number;
  document_count: number;
  updated_at: string;
  progress: { label: string; state: string }[];
  open_points: string[];
  outlook: {
    internet_message_id: string;
    url: string;
    native: boolean;
    saved_draft_id?: string | null;
  };
  communication_sync: string;
  history_partial?: boolean;
  communications?: ProjectCommunication[];
};
export const projectFilters = [
  { key: "all", label: "All orders" },
  { key: "ready", label: "Ready" },
  { key: "handling", label: "In progress" },
  { key: "waiting_customer", label: "Waiting" },
  { key: "needs_you", label: "Needs you" },
] as const;
export function projectMatches(p: ProjectFlow, search: string, filter: string) {
  const state =
    filter === "all" ||
    p.status === filter ||
    (filter === "ready" && p.status === "prepared");
  return (
    state &&
    `${p.name} ${p.number} ${p.customer} ${p.last_communication?.subject || ""}`
      .toLowerCase()
      .includes(search.trim().toLowerCase())
  );
}
