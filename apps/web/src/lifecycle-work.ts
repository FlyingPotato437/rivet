export type LifecycleGate =
  "money" | "scope" | "engineering" | "customer_commitment";
export type LifecycleArtifact = {
  name: string;
  role?: string;
  sha256: string;
  media_type: string;
  url: string;
};
export type DraftBundle = {
  bundle_id: string;
  source_revision: string;
  target_revision: string;
  report_sha256: string;
  artifacts: LifecycleArtifact[];
  changes: {
    id: string;
    target?: string;
    type: string;
    before: string;
    after: string;
    evidence_id?: string;
  }[];
  layout_tasks: {
    id: string;
    instruction?: string;
    text?: string;
    sheet?: string;
    anchor_xyz?: number[];
    geometry_changed?: boolean;
  }[];
};
export type LifecycleComment = {
  comment_id: string;
  kind: "answer" | "text_edit" | "layout_task";
  text: string;
  response: string;
  source_ids: string[];
  draft_bundle?: DraftBundle;
  layout_location?: { sheet?: string; x?: number; y?: number };
};
export type LifecycleLine = {
  tag: string;
  description: string;
  quantity: string | number;
  unit_price: string | number;
  extended_price: string | number;
  currency?: string;
  source_ids?: string[];
  billability_source_ids?: string[];
};
export type LifecycleOutlookHandoff = {
  status: string;
  reply_id?: string;
  receipt_id?: string;
  internet_message_id?: string;
  candidate_count?: number;
  message?: string;
  draft?: {
    id: string;
    status: string;
    attachments: { name?: string }[];
    outcome: string;
    learning_status: string;
    drafted_at: string | null;
    sent_at: string | null;
    error: string;
  } | null;
};
export type LifecyclePackage = {
  id: string;
  operation: string;
  title: string;
  status: string;
  mode: string;
  prepared_at: string;
  evidence_current: boolean;
  fingerprint: string;
  outlook_handoff?: LifecycleOutlookHandoff;
  gates: LifecycleGate[];
  pending_gates: LifecycleGate[];
  blockers: string[];
  sources: Record<
    string,
    { hash: string; document_id: string; document_sha256: string }
  >;
  artifacts: LifecycleArtifact[];
  decisions: {
    gate: LifecycleGate;
    decision: string;
    reason: string;
    actor: string;
    at: string;
    source_ids: string[];
  }[];
  receipts: {
    system: string;
    action: string;
    status: string;
    mode: string;
    notice: string;
  }[];
  payload: {
    revision?: string;
    comments?: LifecycleComment[];
    counts?: { answer: number; text_edit: number; layout_task: number };
    email_draft?: {
      subject: string;
      body: string;
      status: string;
      source_ids?: string[];
    };
    lines?: LifecycleLine[];
    total?: string | number;
    currency?: string;
    exceptions?: { text: string; source_ids: string[] }[];
    change_order?: {
      lines: LifecycleLine[];
      total: string | number;
      status: string;
    };
    part_number?: string;
    lead_time_days?: number;
    current_ship_date?: string;
    proposed_ship_date?: string;
    basis?: string;
    purchase_order_number?: string;
    mismatches?: { [key: string]: unknown }[];
    total_comments?: number;
    documented_resolutions?: number;
    addressed_comments?: number;
    bom_matches?: boolean;
    ready_for_human_signoff?: boolean;
    handover?: string;
    documents?: { id: string; name: string; sha256: string }[];
    source_ids?: string[];
    customer_approval_source_ids?: string[];
    engineering_approval_source_ids?: string[];
  };
};
export type LifecycleView = {
  order_id: string;
  version: number;
  mode: string;
  packages: LifecyclePackage[];
};

export const gateLabels: Record<LifecycleGate, string> = {
  engineering: "Engineering review",
  money: "Price approval",
  scope: "Scope approval",
  customer_commitment: "Customer response approval",
};

export function lifecycleCommercialStatus(p: LifecyclePackage) {
  if (!p.evidence_current) return "Sources changed · review required";
  if (p.status === "rejected") return "Proposal rejected";
  if (p.status === "superseded") return "Replaced proposal";
  if (p.blockers.length || p.status === "blocked")
    return "On hold · conditions unresolved";
  const approved = (gate: LifecycleGate) =>
    p.decisions.some(
      (decision) => decision.gate === gate && decision.decision === "approve",
    );
  const needsPrice = p.pending_gates.includes("money") || !approved("money");
  const needsScope = p.pending_gates.includes("scope") || !approved("scope");
  if (needsPrice && needsScope) return "Scope and price need approval";
  if (needsScope) return "Price reviewed · scope needs approval";
  if (needsPrice) return "Scope reviewed · price needs approval";
  return "Internally reviewed · proposal only";
}

export function lifecycleActorLabel(actor: string) {
  const value = actor.trim();
  if (
    !value ||
    /^(?:user_|usr_|clerk[|:]|auth0\||(?:[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12})$)/i.test(
      value,
    )
  )
    return "Authorized team member";
  return actor;
}

export function lifecycleEvidenceFields(
  text: string,
): { label: string; value: string }[] | null {
  let record: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
      return null;
    record = parsed as Record<string, unknown>;
  } catch {
    return null;
  }
  const entries = Object.entries(record);
  if (
    !entries.length ||
    entries.some(
      ([, value]) =>
        (value !== null &&
          !["string", "number", "boolean"].includes(typeof value) &&
          !(
            Array.isArray(value) &&
            value.every((item) => typeof item === "string")
          )) ||
        (typeof value === "number" &&
          (!Number.isFinite(value) ||
            (Number.isInteger(value) && !Number.isSafeInteger(value)))),
    )
  )
    return null;
  const labels: Record<string, string> = {
    part_number: "Part number",
    unit_cost: "Unit cost",
    unit_price: "Unit price",
    extended_price: "Extended price",
    currency: "Currency",
    gross_margin_percent: "Gross margin",
    lead_time_days: "Lead time (days)",
    baseline_quantity: "Baseline quantity",
    requested_quantity: "Requested quantity",
    billable_scope_tag: "Billable scope item",
    billable: "Billable",
  };
  const currency =
    typeof record.currency === "string" && /^[A-Z]{3}$/.test(record.currency)
      ? record.currency
      : "";
  return entries.map(([key, value]) => {
    let display =
      value === null
        ? "No value"
        : value === true
          ? "Yes"
          : value === false
            ? "No"
            : Array.isArray(value)
              ? value.join("; ")
              : String(value);
    if (
      ["unit_cost", "unit_price", "extended_price", "total"].includes(key) &&
      currency
    ) {
      // Group decimal digits without converting through floating point or rounding.
      const decimal = /^(-?)(\d+)(?:\.(\d+))?$/.exec(display);
      if (decimal)
        display = `${currency} ${decimal[1]}${decimal[2].replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${(decimal[3] || "").padEnd(2, "0")}`;
    } else if (
      key === "gross_margin_percent" &&
      /^-?\d+(?:\.\d+)?$/.test(display)
    )
      display += "%";
    return {
      label:
        labels[key] ||
        key.replaceAll("_", " ").replace(/^./, (first) => first.toUpperCase()),
      value: display,
    };
  });
}

// Relevance comes from the saved work's evidence links, never from inferred text
// keywords. Every other package source remains available, with nothing selected.
export function lifecycleGateEvidence(
  p: LifecyclePackage,
  gate: LifecycleGate,
  availableSourceIds: string[],
) {
  const payload = p.payload;
  const comments = payload.comments || [];
  const lines = payload.change_order?.lines || payload.lines || [];
  let ids: string[] = [];
  if (gate === "money") {
    ids = lines.flatMap((line) => [
      ...(line.source_ids || []),
      ...(line.billability_source_ids || []),
    ]);
  } else if (gate === "scope") {
    ids = [
      ...lines.flatMap((line) => line.billability_source_ids || []),
      ...(payload.exceptions || []).flatMap((entry) => entry.source_ids),
      ...(payload.customer_approval_source_ids || []),
    ];
  } else if (gate === "engineering") {
    ids = [
      ...comments
        .filter((comment) => comment.kind !== "answer")
        .flatMap((comment) => comment.source_ids),
      ...draftBundles(p).flatMap((bundle) =>
        bundle.changes.flatMap((change) =>
          change.evidence_id ? [change.evidence_id] : [],
        ),
      ),
      ...(payload.exceptions || []).flatMap((entry) => entry.source_ids),
      ...(payload.engineering_approval_source_ids || []),
    ];
  } else {
    ids = [
      ...comments
        .filter((comment) => comment.kind === "answer")
        .flatMap((comment) => comment.source_ids),
      ...(payload.email_draft?.source_ids || []),
      ...lines.flatMap((line) => line.source_ids || []),
      ...(payload.source_ids || []),
      ...(payload.customer_approval_source_ids || []),
    ];
  }
  const available = new Set(availableSourceIds.filter((id) => !!p.sources[id]));
  const relevant = [...new Set(ids)].filter((id) => available.has(id));
  const relevantSet = new Set(relevant);
  return {
    relevant,
    additional: [...available].filter((id) => !relevantSet.has(id)),
  };
}

export function lifecycleHandoffStatus(p: LifecyclePackage) {
  const handoff = p.outlook_handoff;
  const status = handoff?.status || "not_requested";
  const draft = handoff?.draft;
  let title = "Response prepared";
  let detail = p.pending_gates.length
    ? "Required approvals are still pending. The prepared response is available below."
    : "No Outlook handoff is recorded for this package.";
  let tone: "neutral" | "working" | "ready" | "attention" | "done" = "neutral";
  if (["queued", "pending", "creating"].includes(status)) {
    title = "Preparing the Outlook draft";
    detail =
      "Rivet is placing the approved response and documents into the linked email thread.";
    tone = "working";
  } else if (status === "created") {
    title = "Ready in Outlook";
    const count = draft?.attachments.length || 0;
    detail = `An unsent reply is saved in the customer’s thread${count ? ` with ${count} ${count === 1 ? "attachment" : "attachments"}` : ""}. Review and send it from Outlook.`;
    tone = "ready";
  } else if (status === "existing_draft") {
    title = "Your Outlook draft is preserved";
    detail =
      "An existing draft was found. Rivet has not replaced it or claimed it as its own work.";
  } else if (status === "sent") {
    title =
      draft?.outcome === "edited"
        ? "Sent after edits"
        : draft?.outcome === "accepted"
          ? "Sent as drafted"
          : "Sent from Outlook";
    detail = "Outlook confirmed the reply was sent.";
    tone = "done";
  } else if (status === "needs_thread") {
    title = "Customer thread needed";
    detail =
      handoff?.candidate_count && handoff.candidate_count > 1
        ? "More than one email thread matches these documents. Link the intended customer thread before creating the draft."
        : "The prepared documents are not linked to a customer email thread yet.";
    tone = "attention";
  } else if (status === "other_thread") {
    title = "Prepared for another thread";
    detail =
      "This package has an Outlook handoff on another captured email. Rivet will not replace a draft or move it to this thread.";
  } else if (status === "awaiting_approval") {
    title = "Ready for your decisions";
    detail =
      "The response and documents are prepared. Rivet will place the unsent Outlook reply after the required decisions are recorded.";
  } else if (["blocked", "failed", "permission_required"].includes(status)) {
    title =
      status === "permission_required"
        ? "Outlook permission needed"
        : "Outlook handoff needs attention";
    detail =
      draft?.error ||
      handoff?.message ||
      "The Outlook draft could not be placed. The prepared files remain available below.";
    tone = "attention";
  } else if (status === "discarded") {
    title = "Outlook draft removed";
    detail =
      "The saved draft is no longer in Outlook. The prepared work remains in this order.";
  } else if (status === "superseded") {
    title = "Earlier Outlook handoff";
    detail =
      "Newer mail replaced this reply. Check the latest customer thread.";
  }
  if (
    !p.evidence_current &&
    !["sent", "discarded", "superseded"].includes(status)
  ) {
    detail +=
      " Source records have changed; this package needs a fresh review before use.";
    tone = "attention";
  }
  return {
    title,
    detail,
    tone,
    canOpen:
      ["created", "existing_draft"].includes(status) &&
      !!handoff?.internet_message_id,
  };
}

export function currentLifecyclePackages(packages: LifecyclePackage[]) {
  return packages.filter((p) => !["superseded", "rejected"].includes(p.status));
}

export function primaryLifecyclePackage(packages: LifecyclePackage[]) {
  const current = currentLifecyclePackages(packages);
  const stages = ["revision_review", "revision_check", "release", "closeout"];
  const meaningful = current.filter(
    (p) => p.evidence_current && stages.includes(p.operation),
  );
  const ranked = [...meaningful].sort((a, b) => {
    const aTime = Date.parse(a.prepared_at),
      bTime = Date.parse(b.prepared_at);
    if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime)
      return bTime - aTime;
    return stages.indexOf(b.operation) - stages.indexOf(a.operation);
  });
  return (
    ranked[0] ||
    current.find((p) => p.operation === "revision_review") ||
    current[0]
  );
}

export function draftBundles(p: LifecyclePackage): DraftBundle[] {
  const bundles = new Map<string, DraftBundle>();
  for (const comment of p.payload.comments || []) {
    if (comment.draft_bundle)
      bundles.set(comment.draft_bundle.bundle_id, comment.draft_bundle);
  }
  return [...bundles.values()];
}

// Only a manifest member of this order may be fetched with workspace credentials.
export function lifecycleArtifactPath(
  orderId: string,
  kind: "artifacts" | "drafting",
  ownerId: string,
  artifact: LifecycleArtifact,
): string | null {
  const parts = [orderId, ownerId, artifact.name];
  if (
    parts.some(
      (part) =>
        !part ||
        /[\\/\u0000-\u001f]/.test(part) ||
        part === "." ||
        part === "..",
    )
  )
    return null;
  if (!/^[a-f0-9]{64}$/i.test(artifact.sha256)) return null;
  const expected = `/api/orders/${orderId}/lifecycle/${kind}/${ownerId}/${artifact.name}`;
  try {
    if (
      artifact.url.includes("?") ||
      artifact.url.includes("#") ||
      decodeURIComponent(artifact.url) !== expected
    )
      return null;
  } catch {
    return null;
  }
  return `/api/orders/${encodeURIComponent(orderId)}/lifecycle/${kind}/${encodeURIComponent(ownerId)}/${encodeURIComponent(artifact.name)}`;
}

export function lifecycleDecisionRequest(
  p: LifecyclePackage,
  version: number,
  fingerprint: string,
  gate: LifecycleGate,
  decision: "approve" | "reject",
  reason: string,
  sourceIds: string[],
  availableSourceIds: string[],
) {
  if (
    p.status !== "awaiting_decision" ||
    !p.evidence_current ||
    p.blockers.length ||
    p.fingerprint !== fingerprint
  )
    throw new Error(
      "This work changed or is blocked. Refresh it before recording a decision.",
    );
  if (
    !p.pending_gates.includes(gate) ||
    p.decisions.some((d) => d.gate === gate)
  )
    throw new Error(
      "This decision has already been recorded or is not required.",
    );
  if (reason.trim().length < 8)
    throw new Error(
      "Add a short reason for this decision (at least 8 characters).",
    );
  const source_ids = [...new Set(sourceIds)];
  if (
    !source_ids.length ||
    source_ids.some((id) => !p.sources[id] || !availableSourceIds.includes(id))
  )
    throw new Error(
      "Select the source evidence you reviewed for this decision.",
    );
  return {
    expected_version: version,
    gate,
    decision,
    reason: reason.trim(),
    source_ids,
  };
}

export function lifecycleStatus(p: LifecyclePackage) {
  if (!p.evidence_current && !["rejected", "superseded"].includes(p.status))
    return "Sources changed";
  return (
    (
      {
        awaiting_decision: "Prepared · needs approval",
        approved: "Approval recorded",
        blocked: "Prepared · on hold",
        rejected: "Rejected",
        superseded: "Replaced",
      } as Record<string, string>
    )[p.status] || "Prepared"
  );
}
