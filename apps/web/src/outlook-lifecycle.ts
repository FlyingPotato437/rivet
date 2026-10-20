import type { MessageContext } from "./outlook-bridge";
import {
  lifecycleDecisionRequest,
  type LifecycleGate,
  type LifecyclePackage,
} from "./lifecycle-work.ts";

export type OutlookLifecycleSource = {
  id: string;
  document_id: string;
  document_name: string;
  role?: string;
  text: string;
  location: { page?: number; line?: number; cells?: string };
  hash: string;
  document_sha256: string;
  current: boolean;
  truncated: boolean;
};
export type OutlookLifecyclePackage = LifecyclePackage & {
  allowed_gates: LifecycleGate[];
};
export type OutlookLifecycleView = {
  status: string;
  internet_message_id: string;
  order_id: string | null;
  order_number?: string;
  receipt_id: string | null;
  revision?: string;
  version: number | null;
  packages: OutlookLifecyclePackage[];
  sources: OutlookLifecycleSource[];
};

export function outlookLifecycleMatches(
  view: OutlookLifecycleView | undefined,
  message: MessageContext | null,
  orderId: string,
) {
  return (
    !!view &&
    !!message &&
    message.mode === "outlook" &&
    !!message.itemKey &&
    !!message.internetMessageId &&
    view.status === "matched" &&
    view.internet_message_id === message.internetMessageId &&
    !!view.receipt_id &&
    !!orderId &&
    view.order_id === orderId &&
    typeof view.version === "number" &&
    view.version >= 1
  );
}

export function outlookPackageSources(
  view: OutlookLifecycleView,
  item: LifecyclePackage,
) {
  return view.sources.filter((source) => {
    const saved = item.sources[source.id];
    return (
      source.current === true &&
      !!saved &&
      source.document_id === saved.document_id &&
      source.hash === saved.hash &&
      source.document_sha256 === saved.document_sha256
    );
  });
}

export function outlookLifecycleDecision({
  initial,
  fresh,
  expectedMessage,
  currentMessage,
  orderId,
  packageId,
  gate,
  decision,
  reason,
  selectedSourceIds,
}: {
  initial: OutlookLifecycleView;
  fresh: OutlookLifecycleView;
  expectedMessage: MessageContext;
  currentMessage: MessageContext;
  orderId: string;
  packageId: string;
  gate: LifecycleGate;
  decision: "approve" | "reject";
  reason: string;
  selectedSourceIds: string[];
}) {
  if (
    currentMessage.mode !== "outlook" ||
    currentMessage.itemKey !== expectedMessage.itemKey ||
    currentMessage.internetMessageId !== expectedMessage.internetMessageId ||
    !outlookLifecycleMatches(initial, expectedMessage, orderId) ||
    !outlookLifecycleMatches(fresh, currentMessage, orderId)
  )
    throw new Error(
      "The selected email or its order match changed. Reopen the decision for this email.",
    );
  if (
    fresh.receipt_id !== initial.receipt_id ||
    fresh.revision !== initial.revision ||
    fresh.version !== initial.version
  )
    throw new Error(
      "The order changed while you were reviewing. Refresh and review the current package.",
    );
  const original = initial.packages.find((item) => item.id === packageId);
  const current = fresh.packages.find((item) => item.id === packageId);
  if (
    !original ||
    !current ||
    original.operation !== current.operation ||
    original.fingerprint !== current.fingerprint ||
    !current.allowed_gates.includes(gate)
  )
    throw new Error(
      "This package changed or this decision requires a different authorized approver.",
    );
  const originalSources = new Map(
    outlookPackageSources(initial, original).map((source) => [
      source.id,
      source,
    ]),
  );
  const available = outlookPackageSources(fresh, current)
    .filter((source) => {
      const saved = originalSources.get(source.id);
      return (
        saved &&
        saved.hash === source.hash &&
        saved.document_sha256 === source.document_sha256
      );
    })
    .map((source) => source.id);
  return {
    ...lifecycleDecisionRequest(
      current,
      fresh.version!,
      original.fingerprint,
      gate,
      decision,
      reason,
      selectedSourceIds,
      available,
    ),
    internet_message_id: fresh.internet_message_id,
    receipt_id: fresh.receipt_id!,
    package_fingerprint: current.fingerprint,
  };
}

export function outlookPackageHandoff(
  item: LifecyclePackage,
  view: OutlookLifecycleView,
) {
  const handoff = item.outlook_handoff;
  if (!handoff?.internet_message_id) return handoff;
  return handoff.internet_message_id === view.internet_message_id &&
    handoff.receipt_id === view.receipt_id
    ? handoff
    : { status: "not_requested" };
}
