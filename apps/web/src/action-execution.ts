export type ActionReceiptFile = {
  document_id: string;
  name: string;
  sha256: string;
  bytes: number;
};
export type ActionAttempt = {
  id: string;
  at: string;
  action_id: string;
  action: string;
  package_id: string;
  status: string;
  message?: string;
  reconciled?: boolean;
  receipt?: {
    mode: string;
    status: string;
    action_id: string;
    remote_version: number;
    package_fingerprint: string;
    source_state_sha256: string;
    result_sha256: string;
    applied_at: string;
    files: ActionReceiptFile[];
  };
};
export type ActionExecutionView = {
  mode: string;
  order_id: string;
  attempts: ActionAttempt[];
  vendor_writes: boolean;
};

const labels: Record<string, { system: string; label: string }> = {
  "sap.draft_sales_order": { system: "ERP", label: "Draft order saved" },
  "sap.propose_change": { system: "ERP", label: "Commercial change proposed" },
  "sap.release_order": { system: "ERP", label: "Production status recorded" },
  "sharepoint.create_job_folder": {
    system: "SharePoint",
    label: "Job folder created",
  },
  "sharepoint.publish_release": {
    system: "SharePoint",
    label: "Released files published",
  },
  "vault.release_revision": {
    system: "Drawing vault",
    label: "Approved revision published",
  },
  "teams.buyer_handoff": {
    system: "Team channel",
    label: "Buyer update posted",
  },
  "schedule.confirm_material_date": {
    system: "Schedule",
    label: "Material date recorded",
  },
};

export function actionLabel(action: string) {
  return labels[action];
}

export function actionExecutionRows(attempts: ActionAttempt[]) {
  const latest = new Map<string, ActionAttempt>();
  for (const attempt of [...attempts].sort((a, b) =>
    b.at.localeCompare(a.at),
  )) {
    if (!latest.has(attempt.action)) latest.set(attempt.action, attempt);
  }
  return Object.entries(labels).flatMap(([action, value]) => {
    const attempt = latest.get(action);
    if (!attempt) return [];
    const applied =
      attempt.status === "applied_local_simulation" &&
      attempt.receipt?.mode === "local_http_demo" &&
      attempt.receipt.status === "applied_local_simulation" &&
      attempt.receipt.action_id === attempt.action_id;
    return [
      {
        ...value,
        action,
        attempt,
        applied,
        waiting: !applied && attempt.status === "receipt_unknown",
      },
    ];
  });
}
