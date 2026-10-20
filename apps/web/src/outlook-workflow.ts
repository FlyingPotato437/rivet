/** Decisions for the Outlook pane. Server preparation remains the source of truth. */
export type NativeOutlookDraft = {
  id?: string;
  status: string;
  attachments: { name: string }[];
  outlook_url?: string;
  ews_item_id?: string;
  rest_item_id?: string;
  error?: string;
};

/** A native draft owns the reply until Outlook records its outcome. */
export function nativeDraftOwnsReply(status?: string) {
  return [
    "pending",
    "creating",
    "created",
    "existing_draft",
    "sent",
    "discarded",
    "superseded",
  ].includes(status || "");
}

export function nativeDraftNotice(status?: string) {
  switch (status) {
    case "pending":
    case "creating":
      return "Rivet is placing this reply in Outlook. It will appear in this thread; no extra step is needed here.";
    case "created":
      return "Your unsent reply is in this Outlook thread. Open the draft to edit it and send when ready.";
    case "existing_draft":
      return "You already have an unsent reply in this thread. Rivet preserved it without changing your text or attachments.";
    case "sent":
      return "Your team sent the reply from Outlook. Rivet keeps the supporting evidence here.";
    case "discarded":
      return "The Outlook draft was removed. Rivet will not recreate it automatically.";
    case "superseded":
      return "This reply has been replaced by newer work. Open the latest customer email to see its reply.";
    default:
      return "";
  }
}

export function safeOutlookUrl(value?: string) {
  try {
    const url = new URL(value || "");
    return url.protocol === "https:" &&
      [
        "outlook.office.com",
        "outlook.office365.com",
        "outlook.live.com",
        "outlook.cloud.microsoft",
      ].includes(url.hostname) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

/** Graph's verified native-draft link opens read mode by default in Outlook Web.
 * Use compose for that same item; never construct a draft from an inbox message.
 */
export function editableNativeDraftUrl(value?: string, restItemId?: string) {
  const safe = safeOutlookUrl(value);
  if (!safe) return null;
  const url = new URL(safe);
  const composeOrigin =
    url.hostname === "outlook.live.com"
      ? url.origin
      : "https://outlook.cloud.microsoft";
  const itemId =
    restItemId ||
    (/^\/owa\/?$/.test(url.pathname) ? url.searchParams.get("ItemID") : "");
  // The server resolves this ID from the already-existing, still-unsent draft.
  // Outlook's native compose route avoids the extra "Continue editing" step.
  if (itemId && itemId.length <= 2048 && /^[A-Za-z0-9+/=_-]+$/.test(itemId)) {
    return `${composeOrigin}/mail/compose/${encodeURIComponent(itemId)}`;
  }
  if (
    [
      "outlook.office.com",
      "outlook.office365.com",
      "outlook.cloud.microsoft",
    ].includes(url.hostname) &&
    /^\/mail\/deeplink\/read\/[^/]+\/?$/.test(url.pathname)
  ) {
    return `${composeOrigin}${url.pathname.replace("/deeplink/read/", "/compose/")}`;
  }
  return url.href;
}

/** Saved replies may predate the separate private workspace deployment. Rebase
 * only Rivet evidence routes; never turn an arbitrary external link into a trusted
 * citation or navigate to a different order/source than the supplied evidence.
 */
export function workspaceSourceUrl(
  value: string,
  workspaceBase: string,
  expected?: { orderId?: string; sourceId?: string },
) {
  try {
    const workspace = new URL(workspaceBase);
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(
      workspace.hostname,
    );
    if (
      workspace.username ||
      workspace.password ||
      !(
        workspace.protocol === "https:" ||
        (local && workspace.protocol === "http:")
      )
    )
      return null;
    const source = new URL(value, workspace);
    const legacyOrigins = [
      "http://127.0.0.1:5178",
      "http://localhost:5178",
      "https://127.0.0.1:5179",
      "https://localhost:5179",
      "https://rivet-usa.com",
      "https://www.rivet-usa.com",
    ];
    if (
      source.username ||
      source.password ||
      (source.origin !== workspace.origin &&
        !legacyOrigins.includes(source.origin))
    )
      return null;
    if (source.pathname !== "/" && source.pathname !== workspace.pathname)
      return null;
    const route = /^#\/?order\/([A-Za-z0-9_-]{1,128})\?(.+)$/.exec(source.hash);
    if (!route) return null;
    const params = new URLSearchParams(route[2]);
    const sourceId = params.get("source") || "";
    if (
      !/^[A-Za-z0-9_-]{1,128}$/.test(sourceId) ||
      params.getAll("source").length !== 1 ||
      (expected?.orderId && route[1] !== expected.orderId) ||
      (expected?.sourceId && sourceId !== expected.sourceId)
    )
      return null;
    const safeParams = new URLSearchParams({ tab: "work", source: sourceId });
    const comment = params.get("comment");
    if (comment && /^[A-Za-z0-9_-]{1,128}$/.test(comment))
      safeParams.set("comment", comment);
    workspace.search = "";
    workspace.hash = `order/${route[1]}?${safeParams}`;
    return workspace.href;
  } catch {
    return null;
  }
}

export function followAutomaticPreparation(
  mailboxConnected?: boolean,
  contextStatus?: string,
  preparationStatus?: string,
) {
  if (!mailboxConnected) return false;
  if (
    [
      "needs_confirmation",
      "set_aside",
      "not_order_related",
      "excluded_draft",
      "excluded_internal_update",
    ].includes(contextStatus || "")
  )
    return false;
  return !["failed", "blocked", "not_needed", "ready", "reviewed"].includes(
    preparationStatus || "",
  );
}

export function initialReplyAttachments(draft: {
  status: string;
  selected_attachments?: { document_id: string }[];
  recommended_attachment_ids?: string[];
  attachment_candidates?: { document_id: string; eligible: boolean }[];
}) {
  // Preserve an explicit saved choice, including an approved reply with no files.
  if (draft.selected_attachments?.length)
    return draft.selected_attachments.map((file) => file.document_id);
  if (draft.status === "approved") return [];
  const recommended = new Set(draft.recommended_attachment_ids || []);
  // Recommendations are produced by the server's released-document policy. A
  // generic eligible internal file must never be selected merely for being usable.
  return (draft.attachment_candidates || [])
    .filter((file) => recommended.has(file.document_id) && file.eligible)
    .map((file) => file.document_id);
}

/** One explicit insert action, with each asynchronous boundary revalidated. */
export async function reviewAndInsertReply<
  Draft extends { status: string },
  Composed,
>({
  draft,
  isCurrent,
  validateMessage,
  canInsert,
  saveReview,
  onReviewed,
  compose,
  insert,
}: {
  draft: Draft;
  isCurrent: () => boolean;
  validateMessage: () => unknown;
  canInsert?: () => Promise<boolean>;
  saveReview: () => Promise<Draft>;
  onReviewed: (saved: Draft) => void;
  compose: (saved: Draft) => Promise<Composed>;
  insert: (composed: Composed) => Promise<unknown>;
}): Promise<Draft | null> {
  if (!isCurrent()) return null;
  validateMessage();
  if (canInsert && !(await canInsert())) return null;
  if (!isCurrent()) return null;
  // Review is durable. Retrying an Outlook/compose failure must not try to approve
  // an already approved record again or discard the user's saved text and files.
  const saved = draft.status === "approved" ? draft : await saveReview();
  if (!isCurrent()) return null;
  validateMessage();
  onReviewed(saved);
  const composed = await compose(saved);
  if (!isCurrent()) return null;
  validateMessage();
  await insert(composed);
  return isCurrent() ? saved : null;
}

export function intakeRevisionForConfirmation(
  detected: { revision?: string; revisions?: string[] },
  selected = "",
) {
  const revisions = [...new Set(detected.revisions || [])];
  if (revisions.length > 1) {
    if (!selected || !revisions.includes(selected))
      throw new Error(
        "Choose the returned revision before linking this email.",
      );
    return selected;
  }
  return detected.revision || revisions[0] || "Unspecified";
}
