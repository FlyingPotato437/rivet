import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  ArrowClockwise,
  Check,
  CircleNotch,
  EnvelopeSimple,
  FileText,
  WarningCircle,
} from "@phosphor-icons/react";
import { api } from "./api";
import { Mark } from "./ui";
import {
  openOutlookReply,
  openSavedOutlookDraft,
  canOpenSavedOutlookDraft,
  openSavedOutlookLink,
  readOutlookContext,
  canAttachOutlookFiles,
  replyHtml,
  requireCapturedOutlookMessage,
  type MessageContext,
  type OfficeRuntime,
} from "./outlook-bridge";
import "./outlook-assistant.css";
import "./fonts.css";
import {
  EquipmentCorrections,
  useEquipmentCorrections,
} from "./EquipmentCorrections";
import {
  correctedBundleReady,
  correctedDraftReady,
  newestCorrectedBundle,
} from "./equipment-correction-types";
import { ConnectedSources, type ConnectedContext } from "./ConnectedSources";
import { GenerationSteps, type GenerationRun } from "./GenerationSteps";
import { CustomerBrief } from "./CustomerBrief";
import { ReplyLearning, type LearningContext } from "./CompanyLearning";
import {
  OutlookLifecycleDecisions,
  useOutlookLifecycle,
} from "./OutlookLifecycleDecisions";
import { outlookLifecycleMatches } from "./outlook-lifecycle";
import {
  initialReplyAttachments,
  reviewAndInsertReply,
  intakeRevisionForConfirmation,
  followAutomaticPreparation,
  nativeDraftOwnsReply,
  nativeDraftNotice,
  safeOutlookUrl,
  workspaceSourceUrl,
  type NativeOutlookDraft,
} from "./outlook-workflow";

type Order = { id: string; number: string; title: string };
type IntakeDecision = {
  intake_id: string;
  status: string;
  detected: { revision?: string; revisions?: string[] };
  candidates: (Order & { reasons?: string[]; identifier_conflict?: boolean })[];
};
type Context = {
  intake_id?: string | null;
  preparation?: {
    status: string;
    detail: string;
    automatic: boolean;
    draft_id?: string;
  };
  prepared_reply?: ReplyDraft | null;
  matched_order: Order | null;
  orders: Order[];
  mailbox_connected: boolean;
  mailbox_status?: "current" | "attention_required" | "not_connected";
  context_status: string;
  connected_context?: ConnectedContext | null;
};
type Attachment = {
  document_id: string;
  name: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  revision: string;
  role: string;
  relationship: string;
  warnings: string[];
  eligible: boolean;
  unavailable_reason?: string;
};
type ComposeReply = {
  body: string;
  internet_message_id: string;
  attachments: {
    document_id: string;
    name: string;
    size_bytes: number;
    sha256: string;
    base64: string;
  }[];
  sent: false;
};
type ReplyDraft = {
  connected_context?: ConnectedContext;
  id: string;
  order_id: string;
  status:
    | "generating"
    | "ready"
    | "needs_context"
    | "failed"
    | "approved"
    | "rejected";
  subject: string;
  body: string;
  source_refs: { id: string; label: string; url: string }[];
  unresolved: string[];
  evidence_hash: string;
  model: string;
  created_at: string;
  origin: "ai" | "rules" | "deterministic";
  prepared_automatically?: boolean;
  recommended_attachment_ids?: string[];
  error?: string;
  coverage: {
    documents_read: number;
    comments_considered: number;
    documents_total?: number;
    comments_total?: number;
    source_excerpts?: number;
  };
  attachment_candidates?: Attachment[];
  selected_attachments?: Attachment[];
  attachment_limits?: {
    max_files: number;
    max_file_bytes: number;
    max_total_bytes: number;
  };
  learning_status?: "pending_review" | "approved" | "not_requested";
  learning_context?: LearningContext;
};

function workspaceLink(orderId?: string) {
  const configured =
    import.meta.env.VITE_WORKSPACE_URL || window.location.origin;
  const url = new URL(configured);
  if (!["https:", "http:"].includes(url.protocol)) return "/";
  url.hash = orderId ? `order/${encodeURIComponent(orderId)}` : "settings";
  return url.href;
}

function sourceLink(value: string, orderId: string, sourceId: string) {
  return workspaceSourceUrl(value, workspaceLink(), { orderId, sourceId });
}

export default function OutlookAssistant() {
  const [message, setMessage] = useState<MessageContext | null>(null);
  const [context, setContext] = useState<Context | null>(null);
  const [orderId, setOrderId] = useState("");
  const [choosing, setChoosing] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [draft, setDraft] = useState<ReplyDraft | null>(null);
  const [run, setRun] = useState<GenerationRun | null>(null);
  const [body, setBody] = useState("");
  const [intake, setIntake] = useState<IntakeDecision | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [revisionChoice, setRevisionChoice] = useState("");
  const [example, setExample] = useState(false);
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const [busy, setBusy] = useState<
    "context" | "generate" | "review" | "open" | ""
  >("context");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [hostWarning, setHostWarning] = useState("");
  const [pollAttempt, setPollAttempt] = useState(0);
  // Autopilot may already have placed this reply in the thread's Outlook drafts.
  const [placed, setPlaced] = useState<NativeOutlookDraft | null>(null);
  const [placementKnown, setPlacementKnown] = useState(false);
  const [placementError, setPlacementError] = useState("");
  const [nativeOpenFallback, setNativeOpenFallback] = useState(false);
  const lifecycle = useOutlookLifecycle(
    message,
    context?.matched_order?.id === orderId ? orderId : "",
  );
  const corrections = useEquipmentCorrections(orderId),
    correctedWork = newestCorrectedBundle(corrections.data);
  const correctedOwnsWork =
      !!correctedWork && correctedBundleReady(correctedWork),
    correctedInOutlook = !!correctedWork && correctedDraftReady(correctedWork);
  const lifecycleOwnsWork =
    outlookLifecycleMatches(lifecycle.data, message, orderId) &&
    !!lifecycle.data?.packages.some(
      (item) =>
        ["quote", "revision_review"].includes(item.operation) &&
        !["rejected", "superseded"].includes(item.status),
    );
  useEffect(() => {
    const id = message?.internetMessageId;
    setPlaced(null);
    setPlacementKnown(false);
    setPlacementError("");
    setNativeOpenFallback(false);
    if (!id || message?.mode !== "outlook") return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const result = await api<{ draft: NativeOutlookDraft | null }>(
          `/autopilot/outlook-draft?internet_message_id=${encodeURIComponent(id)}`,
        );
        if (disposed) return;
        setPlaced((current) =>
          result.draft &&
          current?.id === result.draft.id &&
          current?.status === result.draft.status
            ? { ...current, ...result.draft }
            : result.draft,
        );
        setPlacementKnown(true);
        setPlacementError("");
      } catch {
        if (!disposed) {
          setPlacementKnown(false);
          setPlacementError(
            "Rivet could not check Outlook's saved draft. It will check again shortly.",
          );
        }
      } finally {
        // Placement can complete after a prepared reply is already visible. Poll
        // its status without replacing the user's editable text or source view.
        if (!disposed) timer = setTimeout(refresh, 8000);
      }
    };
    void refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [message?.internetMessageId, message?.mode, message?.itemKey]);
  const office = useRef<OfficeRuntime | undefined>(undefined);
  const sequence = useRef(0);
  const requestLock = useRef(false);

  const loadContext = useCallback(async (next: MessageContext) => {
    const turn = ++sequence.current;
    setMessage(next);
    setPlaced(null);
    setPlacementKnown(false);
    setIntake(null);
    setRevisionChoice("");
    setChoosing(false);
    setInstruction("");
    setDraft(null);
    setRun(null);
    setBody("");

    setExample(false);
    setAttachmentIds([]);
    setNotice("");
    setError("");
    setContext(null);
    setOrderId("");
    setBusy("context");
    try {
      const query = next.internetMessageId
        ? `?internet_message_id=${encodeURIComponent(next.internetMessageId)}`
        : "";
      const result = await api<Context>(`/reply-assistant/context${query}`);
      if (turn !== sequence.current) return;
      setContext(result);
      setOrderId(result.matched_order?.id || "");
      setChoosing(next.mode !== "outlook" && !result.matched_order);
      if (result.prepared_reply) {
        setDraft(result.prepared_reply);
        setBody(result.prepared_reply.body);
        setAttachmentIds(initialReplyAttachments(result.prepared_reply));
      }
    } catch (e) {
      if (turn === sequence.current) setError((e as Error).message);
    } finally {
      if (turn === sequence.current) setBusy("");
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    let runtime: OfficeRuntime | undefined;
    let remove: (() => void) | undefined;
    let waitingForRuntime = true;
    const initialize = async () => {
      try {
        // A blocked CDN must not leave a blank pane indefinitely.
        await Promise.race([
          new Promise<void>((resolve) => {
            const check = () => {
              if (disposed || !waitingForRuntime) return resolve();
              if (window.Office) {
                runtime = window.Office;
                resolve();
              } else window.setTimeout(check, 100);
            };
            check();
          }),
          new Promise<void>((_, reject) =>
            window.setTimeout(
              () =>
                reject(
                  new Error(
                    "Office.js did not load. This preview cannot read or reply to your Outlook message.",
                  ),
                ),
              6000,
            ),
          ),
        ]);
        waitingForRuntime = false;
        if (disposed || !runtime) return;
        const info = await Promise.race([
          runtime.onReady(),
          new Promise<never>((_, reject) =>
            window.setTimeout(
              () =>
                reject(
                  new Error(
                    "Outlook did not respond. Reload the pane in Outlook to reconnect.",
                  ),
                ),
              6000,
            ),
          ),
        ]);
        if (disposed) return;
        if (info.host === "Outlook") office.current = runtime;
        const refresh = () => {
          if (!disposed) void loadContext(readOutlookContext(office.current));
        };
        const mailbox = office.current?.context.mailbox;
        const event = runtime.EventType?.ItemChanged;
        if (event && mailbox?.addHandlerAsync) {
          mailbox.addHandlerAsync(event, refresh, (result) => {
            if (!disposed && result.status !== "succeeded")
              setHostWarning(
                "Rivet could not follow message selection. Reload the pane after switching messages.",
              );
          });
          remove = () =>
            mailbox.removeHandlerAsync?.(event, { handler: refresh });
        }
        refresh();
      } catch (e) {
        waitingForRuntime = false;
        if (!disposed) {
          setHostWarning((e as Error).message);
          void loadContext(readOutlookContext());
        }
      }
    };
    void initialize();
    return () => {
      disposed = true;
      waitingForRuntime = false;
      sequence.current++;
      remove?.();
    };
  }, [loadContext]);

  // Follow the durable worker result; opening the pane never starts a duplicate request.
  useEffect(() => {
    if (
      message?.mode !== "outlook" ||
      !message.internetMessageId ||
      busy ||
      draft ||
      !followAutomaticPreparation(
        context?.mailbox_connected,
        context?.context_status,
        context?.preparation?.status,
      )
    )
      return;
    const turn = sequence.current;
    let disposed = false;
    const timer = window.setTimeout(async () => {
      try {
        const result = await api<Context>(
          `/reply-assistant/context?internet_message_id=${encodeURIComponent(message.internetMessageId)}`,
        );
        const current = readOutlookContext(office.current);
        if (
          disposed ||
          turn !== sequence.current ||
          current.itemKey !== message.itemKey ||
          current.internetMessageId !== message.internetMessageId
        )
          return;
        setContext(result);
        if (result.matched_order) {
          setOrderId(result.matched_order.id);
          setChoosing(false);
        }
        if (result.prepared_reply) {
          setDraft(result.prepared_reply);
          setBody(result.prepared_reply.body);
          setAttachmentIds(initialReplyAttachments(result.prepared_reply));
        }
      } catch (e) {
        if (!disposed && turn === sequence.current)
          setError((e as Error).message);
      }
    }, 5000);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [message, context, busy, draft]);

  useEffect(() => {
    if (!context?.intake_id || context.context_status !== "needs_confirmation")
      return;
    const turn = sequence.current;
    let disposed = false;
    void api<IntakeDecision>(
      `/records/intake/${encodeURIComponent(context.intake_id)}`,
    )
      .then((result) => {
        if (!disposed && turn === sequence.current) setIntake(result);
      })
      .catch((e) => {
        if (!disposed && turn === sequence.current)
          setError((e as Error).message);
      });
    return () => {
      disposed = true;
    };
  }, [context?.intake_id, context?.context_status]);

  async function decideIntake(order: string | null) {
    if (!intake || !message || requestLock.current) return;
    const turn = sequence.current,
      expected = message;
    requestLock.current = true;
    setConfirming(true);
    setError("");
    try {
      const current = readOutlookContext(office.current);
      if (
        current.itemKey !== expected.itemKey ||
        current.internetMessageId !== expected.internetMessageId
      )
        throw new Error(
          "The selected email changed. Reopen Rivet for the current message.",
        );
      await api(
        `/records/intake/${encodeURIComponent(intake.intake_id)}/${order ? "confirm" : "set-aside"}`,
        order
          ? {
              order_id: order,
              revision_label: intakeRevisionForConfirmation(
                intake.detected,
                revisionChoice,
              ),
            }
          : {},
      );
      if (turn === sequence.current) await loadContext(expected);
    } catch (e) {
      if (turn === sequence.current) setError((e as Error).message);
    } finally {
      requestLock.current = false;
      setConfirming(false);
    }
  }

  useEffect(() => {
    if (draft?.status !== "generating") return;
    const turn = sequence.current;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const started = Date.now();
    setBusy("generate");
    const refresh = async () => {
      try {
        const result = await api<ReplyDraft>(
          `/reply-drafts/${encodeURIComponent(draft.id)}`,
        );
        if (disposed || turn !== sequence.current) return;
        if (message?.mode === "outlook") {
          const current = readOutlookContext(office.current);
          if (
            current.itemKey !== message.itemKey ||
            current.internetMessageId !== message.internetMessageId
          ) {
            void loadContext(current);
            return;
          }
        }
        if (result.status !== "generating") {
          setRun((r) =>
            r && !r.finishedAt
              ? {
                  ...r,
                  finishedAt: Date.now(),
                  outcome: result.status,
                  citations: result.source_refs.length,
                  unresolved: result.unresolved.length,
                }
              : r,
          );
          setDraft(result);
          setBody(result.body);
          setAttachmentIds(initialReplyAttachments(result));
          setBusy("");
          if (result.status === "failed" || result.status === "needs_context")
            setError(result.error || "Rivet could not complete this draft.");
        } else {
          timer = setTimeout(
            refresh,
            Date.now() - started > 90000 ? 6000 : 2000,
          );
        }
      } catch (e) {
        if (!disposed && turn === sequence.current) {
          setBusy("");
          setError((e as Error).message);
        }
      }
    };
    timer = setTimeout(refresh, 1500);
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [draft?.id, draft?.status, pollAttempt]);

  async function generate() {
    if (!orderId || requestLock.current) return;
    requestLock.current = true;
    const turn = sequence.current;
    setBusy("generate");
    setError("");
    setNotice("");
    setDraft(null);
    setRun(null);

    setAttachmentIds([]);
    const outlook = message?.mode === "outlook";
    setRun({
      started: Date.now(),
      systems: context?.connected_context?.systems || [],
      ...(outlook ? {} : { email: null, emailAt: Date.now() }),
    });
    try {
      let messageId: string | undefined;
      if (message?.mode === "outlook") {
        const current = readOutlookContext(office.current);
        messageId = requireCapturedOutlookMessage(
          current,
          message,
          context?.matched_order?.id,
          orderId,
        );
        const freshContext = await api<Context>(
          `/reply-assistant/context?internet_message_id=${encodeURIComponent(messageId)}`,
        );
        if (turn !== sequence.current) return;
        setContext(freshContext);
        // Selection and durable order association may change during that request.
        requireCapturedOutlookMessage(
          readOutlookContext(office.current),
          message,
          freshContext.matched_order?.id,
          orderId,
        );
        setRun(
          (r) =>
            r && {
              ...r,
              emailAt: Date.now(),
              email: { subject: message.subject, sender: message.sender },
              systems: freshContext.connected_context?.systems || r.systems,
            },
        );
      }
      const result = await api<ReplyDraft>("/reply-drafts", {
        order_id: orderId,
        ...(messageId ? { internet_message_id: messageId } : {}),
        ...(instruction.trim() ? { instruction: instruction.trim() } : {}),
      });
      if (turn !== sequence.current) return;
      const returned = Date.now();
      setRun(
        (r) =>
          r && {
            ...r,
            sourcesAt: returned,
            connected: result.connected_context,
            coverage: result.coverage,
            model: result.model,
            ...(result.status === "generating"
              ? {}
              : {
                  finishedAt: returned,
                  outcome: result.status,
                  citations: result.source_refs.length,
                  unresolved: result.unresolved.length,
                }),
          },
      );
      setDraft(result);
      setBody(result.body);
      setAttachmentIds(initialReplyAttachments(result));
      if (result.status === "failed" || result.status === "needs_context")
        setError(
          result.error ||
            "Rivet could not prepare this reply. Retry after checking the order's source documents.",
        );
    } catch (e) {
      if (turn === sequence.current) {
        setRun((r) => r && { ...r, failed: true });
        setError((e as Error).message);
      }
    } finally {
      requestLock.current = false;
      if (turn === sequence.current) setBusy("");
    }
  }

  async function review(decision: "approved" | "rejected", openReply = false) {
    if (
      !draft ||
      requestLock.current ||
      (decision === "approved" && !body.trim())
    )
      return;
    requestLock.current = true;
    const turn = sequence.current;
    const itemKey = message?.itemKey || "";
    setBusy("review");
    setError("");
    setNotice("");
    try {
      if (openReply && readOutlookContext(office.current).itemKey !== itemKey)
        throw new Error(
          "The selected email changed. Review the draft for the current message first.",
        );
      const saveReview = () =>
        api<ReplyDraft>(
          `/reply-drafts/${encodeURIComponent(draft.id)}/review`,
          {
            body,
            decision,
            use_as_example: decision === "approved" && example,
            expected_evidence_hash: draft.evidence_hash,
            attachment_ids: decision === "approved" ? attachmentIds : [],
          },
        );
      if (openReply && decision === "approved") {
        const validateMessage = () =>
          requireCapturedOutlookMessage(
            readOutlookContext(office.current),
            message!,
            context?.matched_order?.id,
            orderId,
          );
        const result = await reviewAndInsertReply({
          draft,
          isCurrent: () => turn === sequence.current,
          validateMessage,
          canInsert: async () => {
            const result = await api<{ draft: NativeOutlookDraft | null }>(
              `/autopilot/outlook-draft?internet_message_id=${encodeURIComponent(message!.internetMessageId)}`,
            );
            if (turn !== sequence.current) return false;
            setPlaced(result.draft);
            setPlacementKnown(true);
            if (nativeDraftOwnsReply(result.draft?.status)) {
              setNotice(nativeDraftNotice(result.draft?.status));
              return false;
            }
            return true;
          },
          saveReview,
          onReviewed: (saved) => {
            setDraft(saved);
            setBody(saved.body);
            setBusy("open");
          },
          compose: async (saved) => {
            const messageId = validateMessage();
            const composed = await api<ComposeReply>(
              `/reply-drafts/${encodeURIComponent(saved.id)}/compose`,
              {
                expected_evidence_hash: saved.evidence_hash,
                internet_message_id: messageId,
              },
            );
            if (composed.internet_message_id !== messageId)
              throw new Error(
                "The prepared reply belongs to a different email. Check the selected message again.",
              );
            return composed;
          },
          insert: (composed) =>
            openOutlookReply(
              office.current,
              itemKey,
              composed.body,
              composed.attachments,
            ),
        });
        if (result && turn === sequence.current)
          setNotice(
            "Draft inserted in Outlook. You can edit it there and send when ready.",
          );
        return;
      }
      const saved = draft.status === "approved" ? draft : await saveReview();
      if (turn !== sequence.current) return;
      setDraft(saved);
      setBody(saved.body);
      if (decision === "rejected") {
        setContext(
          (current) =>
            current && {
              ...current,
              preparation: {
                status: "not_needed",
                detail: "You dismissed this reply. Nothing was sent.",
                automatic: false,
              },
            },
        );
        setNotice("Draft dismissed. Nothing was sent.");
      } else
        setNotice(
          saved.learning_status === "pending_review"
            ? "Reviewed response saved. Your team example is waiting for administrator review. Nothing was sent."
            : "Reviewed response saved to Rivet. Nothing was sent.",
        );
    } catch (e) {
      if (turn === sequence.current) setError((e as Error).message);
    } finally {
      requestLock.current = false;
      if (turn === sequence.current) setBusy("");
    }
  }

  async function openPlacedDraft() {
    if (!message?.internetMessageId || requestLock.current) return;
    const expected = message;
    const turn = sequence.current;
    requestLock.current = true;
    setBusy("open");
    setError("");
    setNotice("");
    let resolvedLink: string | null = null;
    try {
      const result = await api<{ draft: NativeOutlookDraft | null }>(
        `/autopilot/outlook-draft/open?internet_message_id=${encodeURIComponent(expected.internetMessageId)}`,
      );
      if (turn !== sequence.current) return;
      const current = readOutlookContext(office.current);
      if (
        current.itemKey !== expected.itemKey ||
        current.internetMessageId !== expected.internetMessageId
      )
        throw new Error(
          "The selected email changed. Open Rivet on the original message to view its reply.",
        );
      setPlaced(result.draft);
      resolvedLink = safeOutlookUrl(result.draft?.outlook_url);
      if (
        !result.draft ||
        !["created", "existing_draft"].includes(result.draft.status)
      ) {
        setNotice(
          nativeDraftNotice(result.draft?.status) ||
            "This reply is no longer in Outlook drafts. Check the latest thread in Outlook.",
        );
        return;
      }
      if (
        (!result.draft.ews_item_id && !result.draft.rest_item_id) ||
        !canOpenSavedOutlookDraft(office.current)
      ) {
        setNativeOpenFallback(true);
        if (resolvedLink) {
          openSavedOutlookLink(office.current, resolvedLink);
          return;
        }
        setNotice(
          "Your reply is saved in Outlook. Open it from the Drafts folder.",
        );
        return;
      }
      await openSavedOutlookDraft(
        office.current,
        expected.itemKey,
        result.draft.ews_item_id || result.draft.rest_item_id!,
        result.draft.ews_item_id ? "ews" : "rest",
      );
    } catch (e) {
      if (turn === sequence.current) {
        setNativeOpenFallback(true);
        if (resolvedLink) {
          openSavedOutlookLink(office.current, resolvedLink);
        } else setError((e as Error).message);
      }
    } finally {
      requestLock.current = false;
      if (turn === sequence.current) setBusy("");
    }
  }

  const selected =
    (message?.mode !== "outlook"
      ? context?.orders.find((order) => order.id === orderId)
      : null) ||
    (context?.matched_order?.id === orderId ? context.matched_order : null);
  const usableDraft = draft && ["ready", "approved"].includes(draft.status);
  const replySizeProblem =
    message?.mode === "outlook" &&
    new TextEncoder().encode(replyHtml(body)).length > 32000
      ? "This response is too long for Outlook. Shorten it before reviewing."
      : "";
  const attachmentTotal =
    draft?.attachment_candidates
      ?.filter((a) => attachmentIds.includes(a.document_id))
      .reduce((total, a) => total + a.size_bytes, 0) || 0;
  const attachmentProblem =
    attachmentIds.length > (draft?.attachment_limits?.max_files || 5)
      ? "Select fewer attachments before continuing."
      : attachmentTotal >
          (draft?.attachment_limits?.max_total_bytes || 20971520)
        ? "The selected files exceed the reply size limit. Remove a file before continuing."
        : message?.mode === "outlook" &&
            attachmentIds.length > 0 &&
            !canAttachOutlookFiles(office.current)
          ? "This Outlook client cannot insert these files. Uncheck them to prepare a body-only reply."
          : "";
  const native = message?.mode === "outlook";
  const nativeOwnsReply = native && nativeDraftOwnsReply(placed?.status);
  const ResultContainer = nativeOwnsReply ? "details" : "section";
  const nativeDraftUrl = safeOutlookUrl(placed?.outlook_url);
  const hasOpenableNativeDraft =
    native && ["created", "existing_draft"].includes(placed?.status || "");
  const openTarget = useQuery({
    queryKey: [
      "outlook-native-open-target",
      message?.internetMessageId,
      placed?.id,
      placed?.status,
    ],
    queryFn: () =>
      api<{ draft: NativeOutlookDraft | null }>(
        `/autopilot/outlook-draft/open?internet_message_id=${encodeURIComponent(message!.internetMessageId)}`,
      ),
    enabled:
      !!message?.internetMessageId && !!placed?.id && hasOpenableNativeDraft,
    staleTime: 5 * 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  });
  useEffect(() => {
    const saved = openTarget.data?.draft;
    if (!saved) return;
    // Resolve once for this native draft, so web Outlook gets a real one-click
    // link. The regular status poll stays database-only and never calls Graph.
    setPlaced((current) =>
      current?.id === saved.id ? { ...current, ...saved } : current,
    );
    setNativeOpenFallback(
      (!saved.ews_item_id && !saved.rest_item_id) ||
        !canOpenSavedOutlookDraft(office.current),
    );
  }, [openTarget.data]);
  const nativeReady =
    !!message?.itemKey &&
    !!message?.internetMessageId &&
    context?.matched_order?.id === orderId;
  const changeOrder = (id: string) => {
    sequence.current++;
    setOrderId(id);
    setChoosing(!id);
    setDraft(null);
    setRun(null);

    setExample(false);
    setAttachmentIds([]);
    setNotice("");
    setError("");
  };

  const waitingForWork =
    native &&
    !!message?.internetMessageId &&
    !draft &&
    followAutomaticPreparation(
      context?.mailbox_connected,
      context?.context_status,
      context?.preparation?.status,
    );
  const revisionConflict = (intake?.detected.revisions?.length || 0) > 1;
  const candidate =
    intake?.candidates.length === 1 && !intake.candidates[0].identifier_conflict
      ? intake.candidates[0]
      : null;
  const intakeUrl = new URL(workspaceLink());
  intakeUrl.hash = context?.intake_id
    ? `inbox?intake=${encodeURIComponent(context.intake_id)}`
    : "inbox";

  return (
    <main className="outlook-assistant">
      <header className="oa-header">
        <div className="oa-brand">
          <Mark />
          <span>
            rivet<span>.</span>
          </span>
        </div>
        <a
          href={workspaceLink(orderId)}
          target="_blank"
          rel="noopener noreferrer"
          className="oa-workspace"
          aria-label="Open detailed order in Rivet"
        >
          Open order <ArrowUpRight size={15} />
        </a>
      </header>
      <div className="oa-content">
        <section className="oa-heading">
          <span className="oa-mode">
            <span />
            {native
              ? selected?.number || "In your Outlook"
              : "Workspace preview"}
          </span>
          <h1>
            {correctedInOutlook
              ? "Corrected draft ready"
              : correctedOwnsWork
                ? "Corrected files, checked"
                : nativeOwnsReply
                  ? placed?.status === "sent"
                    ? "Reply sent"
                    : placed?.status === "existing_draft"
                      ? "Your Outlook draft"
                      : placed?.status === "created"
                        ? "Ready in Outlook"
                        : ["pending", "creating"].includes(placed?.status || "")
                          ? "Finishing your Outlook draft"
                          : "Reply history"
                  : lifecycleOwnsWork
                    ? "Prepared work for this email"
                    : usableDraft
                      ? draft?.status === "approved"
                        ? "Your reviewed reply"
                        : "Your reply is ready"
                      : native &&
                          context?.context_status === "needs_confirmation"
                        ? "One quick question"
                        : native &&
                            context?.preparation?.status === "not_needed"
                          ? "Nothing needed here"
                          : "Reply with Rivet"}
          </h1>
          <p>
            {native
              ? nativeOwnsReply && placed?.status === "created"
                ? "Your reply is prepared in the conversation. Review it, then send from Outlook."
                : nativeOwnsReply && placed?.status === "existing_draft"
                  ? "Your existing draft is preserved. Rivet keeps its supporting work here."
                  : "Rivet checks the order and prepares the work. You make the decisions."
              : "Preview with saved order documents. Open in Outlook to work with an email."}
          </p>
        </section>
        {hostWarning && <p className="oa-host-note">{hostWarning}</p>}
        {native && (
          <section className="oa-message" aria-label="Selected Outlook email">
            <EnvelopeSimple size={20} />
            <div>
              <h2>{message.subject || "Select an email in Outlook"}</h2>
              {message.sender && <p>{message.sender}</p>}
              {selected && (
                <a
                  className="oa-matched-order"
                  href={workspaceLink(orderId)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Check size={13} />
                  {selected.number || selected.title}
                </a>
              )}
            </div>
          </section>
        )}
        {context && !native && (
          <section className="oa-order" aria-label="Order context">
            <label htmlFor="oa-order-select">Choose an order to preview</label>
            <select
              id="oa-order-select"
              value={orderId}
              disabled={!!busy}
              onChange={(e) => changeOrder(e.target.value)}
            >
              <option value="">Select an order</option>
              {context.orders.map((order) => (
                <option key={order.id} value={order.id}>
                  {order.number ? `${order.number} · ` : ""}
                  {order.title}
                </option>
              ))}
            </select>
          </section>
        )}
        {native &&
          context?.context_status === "needs_confirmation" &&
          !nativeReady && (
            <section
              className="oa-order-question"
              aria-label="Confirm the email’s order"
            >
              <h2>
                {candidate
                  ? `Add this email to ${candidate.number || candidate.title}?`
                  : "Is this email about an order?"}
              </h2>
              <p>
                {candidate
                  ? `Rivet found a possible match${intake?.detected.revision ? ` · Rev ${intake.detected.revision}` : ""}.`
                  : "Rivet could not reliably match it. This is the detail needed to continue."}
              </p>
              {revisionConflict && (
                <div className="oa-choose-order">
                  <label htmlFor="oa-confirm-revision">
                    Which returned revision should this email use?
                  </label>
                  <select
                    id="oa-confirm-revision"
                    value={revisionChoice}
                    onChange={(e) => setRevisionChoice(e.target.value)}
                    disabled={confirming}
                  >
                    <option value="">Choose a revision</option>
                    {intake?.detected.revisions?.map((revision) => (
                      <option key={revision} value={revision}>
                        Rev {revision}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="oa-decision-actions">
                <button
                  type="button"
                  className="oa-primary"
                  disabled={
                    confirming ||
                    !intake ||
                    (!!candidate && revisionConflict && !revisionChoice)
                  }
                  onClick={() =>
                    candidate
                      ? void decideIntake(candidate.id)
                      : setChoosing(true)
                  }
                >
                  {confirming ? "Saving…" : "Yes"}
                </button>
                <button
                  type="button"
                  disabled={confirming || !intake}
                  onClick={() => void decideIntake(null)}
                >
                  No, set aside
                </button>
              </div>
              {choosing && !candidate && (
                <div className="oa-choose-order">
                  <label htmlFor="oa-confirm-order">Which order?</label>
                  <select
                    id="oa-confirm-order"
                    value={orderId}
                    onChange={(e) => setOrderId(e.target.value)}
                    disabled={confirming}
                  >
                    <option value="">Choose an order</option>
                    {context.orders.map((order) => (
                      <option key={order.id} value={order.id}>
                        {order.number} · {order.title}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="oa-primary"
                    disabled={
                      !orderId ||
                      confirming ||
                      (revisionConflict && !revisionChoice)
                    }
                    onClick={() => void decideIntake(orderId)}
                  >
                    Use this order
                  </button>
                </div>
              )}
              <a
                href={intakeUrl.href}
                target="_blank"
                rel="noopener noreferrer"
              >
                {candidate
                  ? "Choose a different order"
                  : "New order or more details"}
              </a>
            </section>
          )}
        {native &&
          context &&
          context.preparation?.status === "not_needed" &&
          !draft && (
            <section className="oa-quiet-state">
              <Check size={19} />
              <div>
                <h2>No order work queued</h2>
                <p>
                  {context.preparation?.detail ||
                    "This message stays out of your review list."}
                </p>
                {context.intake_id && (
                  <a
                    href={intakeUrl.href}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Link it to an order instead
                  </a>
                )}
              </div>
            </section>
          )}
        {native && !message.internetMessageId && (
          <p className="oa-host-note">
            Select a received email in Outlook to prepare its reply.
          </p>
        )}
        {native && context && !context.mailbox_connected && (
          <div className="oa-context-note">
            <p>
              {context.mailbox_status === "attention_required"
                ? "Mailbox sync needs attention. Rivet may not have the latest messages."
                : "Connect your mailbox once so Rivet can prepare replies in the background."}
            </p>
            <a href={workspaceLink()} target="_blank" rel="noopener noreferrer">
              Open connection settings
            </a>
          </div>
        )}
        {nativeReady &&
          !draft &&
          ["blocked", "failed"].includes(
            context?.preparation?.status || "",
          ) && (
            <div className="oa-error" role="status">
              <WarningCircle size={19} />
              <div>
                <strong>Rivet needs one thing before continuing</strong>
                <p>{context?.preparation?.detail}</p>
              </div>
            </div>
          )}
        {busy === "context" && (
          <div className="oa-loading" role="status">
            <span className="oa-skeleton" />
            <span className="oa-skeleton" />
            Loading your order context…
          </div>
        )}
        {error && (
          <div className="oa-error" role="alert">
            <WarningCircle size={19} />
            <div>
              <strong>Could not finish this step</strong>
              <p>{error}</p>
              {(native || !context) && (
                <button
                  type="button"
                  onClick={() =>
                    void loadContext(
                      native
                        ? readOutlookContext(office.current)
                        : message || readOutlookContext(),
                    )
                  }
                >
                  {native ? "Refresh selected email" : "Try again"}
                </button>
              )}
            </div>
          </div>
        )}
        {notice && (
          <div className="oa-notice" role="status">
            <Check size={19} />
            <p>{notice}</p>
          </div>
        )}
        {nativeOwnsReply && (
          <section
            className={`oa-native-output is-${placed?.status || "unknown"}`}
            role="status"
            aria-label="Outlook draft outcome"
          >
            {placed?.status === "created" || placed?.status === "sent" ? (
              <Check size={19} />
            ) : ["pending", "creating"].includes(placed?.status || "") ? (
              <CircleNotch size={19} className="project-spinning" />
            ) : (
              <EnvelopeSimple size={19} />
            )}
            <div>
              <h2>
                {placed?.status === "created"
                  ? "Draft saved in the thread"
                  : placed?.status === "sent"
                    ? "Sent from Outlook"
                    : placed?.status === "existing_draft"
                      ? "Your draft, preserved"
                      : ["pending", "creating"].includes(placed?.status || "")
                        ? "Preparing the Outlook draft"
                        : "Saved draft history"}
              </h2>
              <p>{nativeDraftNotice(placed?.status)}</p>
              {!!placed?.attachments.length &&
                ["created", "sent"].includes(placed.status) && (
                  <div
                    className="oa-output-files"
                    aria-label="Files in the Outlook draft"
                  >
                    {placed.attachments.map((file) => (
                      <div key={file.name}>
                        <FileText size={15} />
                        <span>{file.name}</span>
                      </div>
                    ))}
                  </div>
                )}
              {["created", "existing_draft"].includes(placed?.status || "") &&
              !nativeOpenFallback ? (
                <button
                  className="oa-primary oa-open-native"
                  type="button"
                  disabled={!!busy || openTarget.isFetching}
                  onClick={() => void openPlacedDraft()}
                >
                  {openTarget.isFetching
                    ? "Locating saved draft…"
                    : busy === "open"
                      ? "Opening Outlook draft…"
                      : "Open draft in Outlook"}
                  {!busy && <ArrowUpRight size={16} />}
                </button>
              ) : (
                nativeDraftUrl &&
                ["created", "existing_draft"].includes(
                  placed?.status || "",
                ) && (
                  <a
                    className="oa-primary oa-open-native"
                    href={nativeDraftUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open draft in Outlook <ArrowUpRight size={16} />
                  </a>
                )
              )}
            </div>
          </section>
        )}
        {orderId && (!native || nativeReady) && (
          <EquipmentCorrections id={orderId} compact />
        )}
        {nativeReady && message && (
          <OutlookLifecycleDecisions
            key={`${message.itemKey}:${message.internetMessageId}`}
            message={message}
            orderId={orderId}
            query={lifecycle}
            readCurrentMessage={() => readOutlookContext(office.current)}
            workspaceBase={workspaceLink()}
            onOpenNativeDraft={() => void openPlacedDraft()}
          />
        )}
        {native && placementError && (
          <p className="oa-host-note" role="status">
            {placementError}
          </p>
        )}
        {native && placed?.status === "permission_required" && (
          <div className="oa-error" role="status">
            <WarningCircle size={19} />
            <div>
              <strong>Mailbox permission needs updating</strong>
              <p>
                Your reply is prepared. A Microsoft administrator must allow
                Rivet to save drafts before they can appear automatically.
              </p>
              <a
                href={workspaceLink()}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open mailbox settings
              </a>
              <details>
                <summary>Administrator details</summary>
                <p>
                  {placed.error ||
                    "Grant Microsoft Graph Mail.ReadWrite for this mailbox. Mail.Send is not required."}
                </p>
              </details>
            </div>
          </div>
        )}
        {native && placed?.status === "failed" && (
          <p className="oa-host-note" role="status">
            {placed.error ||
              "Rivet could not place the draft in Outlook yet. Your prepared response is saved below."}
          </p>
        )}
        {!usableDraft &&
          !correctedOwnsWork &&
          !lifecycleOwnsWork &&
          !nativeOwnsReply &&
          draft?.status !== "generating" &&
          busy !== "generate" &&
          context &&
          !waitingForWork &&
          (!native ||
            (nativeReady && context.preparation?.status !== "not_needed")) && (
            <section className="oa-compose-request">
              <details className="oa-optional-direction">
                <summary>
                  Add a direction <span>Optional</span>
                </summary>
                <label htmlFor="oa-instruction">
                  What should Rivet focus on?
                </label>
                <textarea
                  id="oa-instruction"
                  rows={2}
                  maxLength={2000}
                  value={instruction}
                  onChange={(e) => setInstruction(e.target.value)}
                  placeholder="For example, ask for the missing E-10 revision."
                  disabled={!!busy}
                />
              </details>
              <button
                className="oa-primary"
                type="button"
                disabled={!orderId || !!busy || (native && !nativeReady)}
                onClick={() => void generate()}
              >
                <Mark />
                Prepare reply
              </button>
              <p className="oa-caption">
                Rivet prepares a draft. You review it before using it.
              </p>
            </section>
          )}
        {run && busy === "generate" && <GenerationSteps run={run} />}
        {waitingForWork && !lifecycleOwnsWork && busy !== "context" && (
          <section className="oa-progress" role="status" aria-live="polite">
            <span className="oa-progress-line" />
            <h2>
              {nativeReady ? "Preparing your reply" : "Checking this email"}
            </h2>
            <p>
              {context?.preparation?.detail ||
                (nativeReady
                  ? "Rivet is reading the order documents and preparing a reply with sources."
                  : "Rivet is checking the connected mailbox for this message. You can keep working in Outlook.")}
            </p>
            <p className="oa-caption">
              This updates automatically. Nothing is sent.
            </p>
          </section>
        )}
        {!run && busy === "generate" && (
          <section className="oa-progress" role="status" aria-live="polite">
            <span className="oa-progress-line" />
            <h2>Preparing your response</h2>
            <p>
              Checking the saved order context, source references, and
              unresolved questions.
            </p>
            <p className="oa-caption">
              This may take a moment. No email is being sent.
            </p>
          </section>
        )}
        {draft?.status === "generating" && busy !== "generate" && (
          <section className="oa-pending-draft">
            <button
              className="oa-primary"
              type="button"
              onClick={() => {
                setError("");
                setNotice("");
                setPollAttempt((attempt) => attempt + 1);
              }}
            >
              <ArrowClockwise size={17} />
              Check saved draft
            </button>
            <p className="oa-caption">
              The original request stays in Rivet’s work queue.
            </p>
          </section>
        )}
        {usableDraft && !lifecycleOwnsWork && !correctedOwnsWork && (
          <ResultContainer className="oa-result" aria-label="Prepared response">
            {nativeOwnsReply && (
              <summary className="oa-result-summary">
                {placed?.status === "existing_draft"
                  ? "Rivet’s suggestion · not inserted"
                  : "Prepared reply and sources"}
              </summary>
            )}
            <div className="oa-result-top">
              <span>
                {draft.origin === "ai"
                  ? "AI-prepared draft"
                  : "Prepared from recorded information"}
              </span>
              <span>
                {draft.prepared_automatically
                  ? "Prepared automatically"
                  : "Draft"}
              </span>
            </div>
            {draft.unresolved.length > 0 && (
              <details className="oa-unresolved">
                <summary>
                  <WarningCircle size={17} />
                  {draft.unresolved.length}{" "}
                  {draft.unresolved.length === 1 ? "open point" : "open points"}
                </summary>
                <ul>
                  {draft.unresolved.map((question, index) => (
                    <li key={index}>{question}</li>
                  ))}
                </ul>
                <p>
                  These remain open questions in the draft. Rivet has not
                  assumed an answer.
                </p>
              </details>
            )}
            <label className="oa-draft-label" htmlFor="oa-reply">
              {nativeOwnsReply ? "Prepared version" : "Reply"}{" "}
              <span>
                {nativeOwnsReply
                  ? "Edits stay in Outlook"
                  : "Edit here or in Outlook"}
              </span>
            </label>
            <textarea
              id="oa-reply"
              className="oa-reply"
              maxLength={10000}
              value={body}
              rows={Math.min(16, Math.max(8, body.split("\n").length + 2))}
              onChange={(e) => {
                setBody(e.target.value);
              }}
              readOnly={!!nativeOwnsReply}
              disabled={!!busy || draft.status === "approved"}
            />
            {replySizeProblem && !nativeOwnsReply && (
              <p className="oa-attachment-warning" role="alert">
                {replySizeProblem}
              </p>
            )}
            <div className="oa-review-controls">
              {(
                nativeOwnsReply
                  ? placed?.attachments.length
                  : attachmentIds.length
              ) ? (
                <div className="oa-included-files">
                  <span>Included with your draft</span>
                  {(nativeOwnsReply
                    ? placed!.attachments
                    : (draft.attachment_candidates || []).filter((file) =>
                        attachmentIds.includes(file.document_id),
                      )
                  ).map((file) => (
                    <p key={file.name}>
                      <FileText size={15} />
                      {file.name}
                    </p>
                  ))}
                </div>
              ) : null}
              {attachmentProblem && !nativeOwnsReply && (
                <p className="oa-attachment-warning" role="alert">
                  {attachmentProblem}
                </p>
              )}
              {!nativeOwnsReply && (
                <button
                  className="oa-primary"
                  type="button"
                  disabled={
                    !body.trim() ||
                    !!replySizeProblem ||
                    !!attachmentProblem ||
                    !!busy ||
                    (native && !placementKnown) ||
                    (draft.status === "approved" && !message?.canReply)
                  }
                  onClick={() => void review("approved", !!message?.canReply)}
                >
                  {busy === "review" || busy === "open"
                    ? "Inserting draft…"
                    : message?.canReply
                      ? "Insert draft in Outlook"
                      : draft.status === "approved"
                        ? "Response saved"
                        : "Save reviewed response"}
                  {!busy && message?.canReply && <ArrowUpRight size={18} />}
                </button>
              )}
              <p className="oa-caption">
                {nativeOwnsReply
                  ? placed?.status === "existing_draft"
                    ? "This prepared version is for reference. Rivet has not inserted it into or changed your existing Outlook draft."
                    : "This is Rivet’s prepared version. The editable draft and any changes you make are in Outlook."
                  : native && !placementKnown
                    ? "Checking whether this reply is already in Outlook…"
                    : message?.canReply
                      ? "Creates an unsent reply with the selected files. Sending stays in Outlook."
                      : "Saved in Rivet only. Open this add-in in Outlook to reply in the original thread."}
              </p>
              {!nativeOwnsReply &&
                (draft.attachment_candidates?.length || 0) > 0 && (
                  <details className="oa-attachments">
                    <summary>
                      <FileText size={17} /> Attach documents{" "}
                      <span>
                        {attachmentIds.length
                          ? `${attachmentIds.length} selected`
                          : "Optional"}
                      </span>
                    </summary>
                    <p>
                      Rivet suggests relevant released files when available.
                      Selected files are inserted only when you use the draft.
                    </p>
                    {native && !canAttachOutlookFiles(office.current) && (
                      <p className="oa-attachment-warning">
                        This Outlook version does not support inserting files.
                        You can still open a reply without attachments.
                      </p>
                    )}
                    <div>
                      {draft.attachment_candidates!.map((file) => (
                        <label className="oa-attachment" key={file.document_id}>
                          <input
                            type="checkbox"
                            checked={attachmentIds.includes(file.document_id)}
                            disabled={
                              !!busy ||
                              draft.status === "approved" ||
                              !file.eligible ||
                              (native && !canAttachOutlookFiles(office.current))
                            }
                            onChange={(event) => {
                              setAttachmentIds((ids) =>
                                event.target.checked
                                  ? [...ids, file.document_id]
                                  : ids.filter((id) => id !== file.document_id),
                              );
                            }}
                          />
                          <span>
                            <strong>{file.name}</strong>
                            <small>
                              {file.revision ? `Rev ${file.revision} · ` : ""}
                              {file.size_bytes > 0
                                ? `${file.size_bytes < 1024 ? "<1" : (file.size_bytes / 1024).toFixed(0)} KB · `
                                : ""}
                              {file.relationship || file.role}
                            </small>
                            {file.warnings?.map((warning, index) => (
                              <small
                                className="oa-attachment-warning"
                                key={index}
                              >
                                {warning}
                              </small>
                            ))}
                            {!file.eligible && (
                              <small className="oa-attachment-warning">
                                {file.unavailable_reason ||
                                  "Unavailable for this reply"}
                              </small>
                            )}
                          </span>
                        </label>
                      ))}
                    </div>
                    {attachmentProblem && (
                      <p className="oa-attachment-warning" role="alert">
                        {attachmentProblem}
                      </p>
                    )}
                  </details>
                )}
              <details className="oa-sources">
                <summary>
                  <FileText size={17} />
                  Sources and coverage <span>{draft.source_refs.length}</span>
                </summary>
                <p>
                  Evidence from {draft.coverage.documents_read}
                  {draft.coverage.documents_total
                    ? ` of ${draft.coverage.documents_total}`
                    : ""}{" "}
                  documents · {draft.coverage.comments_considered} comments
                  considered
                </p>
                <p>
                  Uses available text and annotations. Drawing geometry has not
                  been verified.
                </p>
                {draft.source_refs.length > 0 ? (
                  <ol>
                    {draft.source_refs.map((source, index) => {
                      const url = sourceLink(
                        source.url,
                        draft.order_id,
                        source.id,
                      );
                      return (
                        <li key={`${source.id}-${index}`}>
                          {url ? (
                            <a
                              href={url}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              {source.label}
                              <ArrowUpRight size={14} />
                            </a>
                          ) : (
                            <span>{source.label}</span>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                ) : (
                  <p>
                    No supporting sources were returned. Confirm the wording
                    before using it.
                  </p>
                )}
              </details>
              {!nativeOwnsReply && (
                <details className="oa-learning">
                  <summary>Team preferences</summary>
                  <label className="oa-check">
                    <input
                      type="checkbox"
                      checked={example}
                      onChange={(e) => setExample(e.target.checked)}
                      disabled={!!busy || draft.status === "approved"}
                    />
                    <span>Save this approved response for team learning.</span>
                  </label>
                  <p>
                    An administrator reviews examples before Rivet uses them for
                    your team. This does not train a shared model.
                  </p>
                </details>
              )}
              {!nativeOwnsReply && (
                <details className="oa-refine">
                  <summary>Change the draft</summary>
                  <label htmlFor="oa-refine-instruction">
                    What should Rivet change?
                  </label>
                  <textarea
                    id="oa-refine-instruction"
                    rows={2}
                    maxLength={2000}
                    value={instruction}
                    onChange={(e) => setInstruction(e.target.value)}
                    placeholder="Make it shorter, or focus on a specific question…"
                    disabled={!!busy}
                  />
                  <button
                    type="button"
                    className="oa-primary"
                    onClick={() => void generate()}
                    disabled={!!busy}
                  >
                    <ArrowClockwise size={16} />
                    Prepare revised draft
                  </button>
                </details>
              )}
              {!nativeOwnsReply && (
                <div className="oa-secondary-actions">
                  <button
                    type="button"
                    onClick={() => void review("rejected")}
                    disabled={!!busy || draft.status === "approved"}
                  >
                    Dismiss draft
                  </button>
                </div>
              )}
            </div>
          </ResultContainer>
        )}
        {usableDraft && <ReplyLearning context={draft.learning_context} />}
        {orderId && (!native || nativeReady) && (
          <details className="oa-evidence">
            <summary>Order context and Rivet’s work</summary>
            {run && busy !== "generate" && <GenerationSteps run={run} />}
            <ConnectedSources
              orderId={orderId}
              snapshot={usableDraft ? draft.connected_context : null}
              workspace={workspaceLink()}
            />
            <CustomerBrief
              key={`customer:${orderId}`}
              orderId={orderId}
              workspace={workspaceLink()}
            />
          </details>
        )}
      </div>
      <footer className="oa-footer">
        Engineering and commercial decisions stay with your team.
      </footer>
    </main>
  );
}
