// Keep the Office boundary small. The runtime comes from Microsoft's Office.js CDN.
type OfficeResult = { status: string; error?: { message?: string } };
type OutlookReplyData = {
  htmlBody: string;
  attachments: {
    type: "base64";
    name: string;
    base64file: string;
    inLine: false;
  }[];
};
export type ReplyFile = { name: string; base64: string };
type ReadMessage = {
  itemId?: string;
  internetMessageId?: string;
  subject?: string;
  from?: { displayName?: string; emailAddress?: string };
  displayReplyFormAsync?: (
    body: string | OutlookReplyData,
    done: (result: OfficeResult) => void,
  ) => void;
};
export type OfficeRuntime = {
  onReady: () => Promise<{ host: string | null }>;
  EventType?: { ItemChanged: string };
  context: {
    ui?: { openBrowserWindow?: (url: string) => void };
    requirements?: {
      isSetSupported: (name: string, version: string) => boolean;
    };
    mailbox?: {
      item?: ReadMessage;
      displayMessageFormAsync?: (
        itemId: string,
        done: (result: OfficeResult) => void,
      ) => void;
      convertToEwsId?: (id: string, version: string) => string;
      addHandlerAsync?: (
        event: string,
        handler: () => void,
        done?: (result: OfficeResult) => void,
      ) => void;
      removeHandlerAsync?: (
        event: string,
        options: { handler: () => void },
      ) => void;
    };
  };
};
declare global {
  interface Window {
    Office?: OfficeRuntime;
  }
}

export type MessageContext = {
  mode: "outlook" | "preview";
  itemKey: string;
  internetMessageId: string;
  subject: string;
  sender: string;
  canReply: boolean;
};

export function readOutlookContext(office?: OfficeRuntime): MessageContext {
  const item = office?.context.mailbox?.item;
  return {
    mode: office?.context.mailbox ? "outlook" : "preview",
    itemKey: item?.itemId || item?.internetMessageId || "",
    internetMessageId: item?.internetMessageId || "",
    subject: typeof item?.subject === "string" ? item.subject : "",
    sender: item?.from?.displayName || item?.from?.emailAddress || "",
    canReply:
      !!item?.displayReplyFormAsync &&
      !!office?.context.requirements?.isSetSupported("Mailbox", "1.9"),
  };
}

export function requireCapturedOutlookMessage(
  current: MessageContext,
  expected: MessageContext,
  matchedOrderId: string | null | undefined,
  selectedOrderId: string,
): string {
  if (
    current.mode !== "outlook" ||
    !current.itemKey ||
    !current.internetMessageId
  )
    throw new Error(
      "Select a received Outlook email with a saved message identity before generating a response.",
    );
  if (
    current.itemKey !== expected.itemKey ||
    current.internetMessageId !== expected.internetMessageId
  )
    throw new Error(
      "The selected email changed. Check its order context again before generating a response.",
    );
  if (!matchedOrderId || matchedOrderId !== selectedOrderId)
    throw new Error(
      "This email has not been captured against the selected order. Wait for mailbox sync or confirm its order in Rivet, then check again.",
    );
  return current.internetMessageId;
}

export function replyHtml(body: string): string {
  const escaped = body.replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ]!,
  );
  return `<div>${escaped.replace(/\r?\n/g, "<br>")}</div>`;
}

export function canAttachOutlookFiles(office?: OfficeRuntime): boolean {
  return !!office?.context.requirements?.isSetSupported("Mailbox", "1.15");
}

export function canOpenSavedOutlookDraft(office?: OfficeRuntime): boolean {
  return (
    !!office?.context.mailbox?.displayMessageFormAsync &&
    !!office.context.requirements?.isSetSupported("Mailbox", "1.9")
  );
}

/** Desktop Office can open the real browser link; web Outlook uses an ordinary link. */
export function openSavedOutlookLink(
  office: OfficeRuntime | undefined,
  value: string,
): boolean {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      ![
        "outlook.office.com",
        "outlook.office365.com",
        "outlook.live.com",
        "outlook.cloud.microsoft",
      ].includes(url.hostname)
    )
      return false;
    if (
      !office?.context.ui?.openBrowserWindow ||
      !office.context.requirements?.isSetSupported(
        "OpenBrowserWindowApi",
        "1.1",
      )
    )
      return false;
    office.context.ui.openBrowserWindow(url.href);
    return true;
  } catch {
    return false;
  }
}

/** Opens the existing native draft; never creates another reply or changes its text. */
export async function openSavedOutlookDraft(
  office: OfficeRuntime | undefined,
  expectedItemKey: string,
  ewsItemId: string,
  idFormat: "ews" | "rest" = "ews",
): Promise<void> {
  const current = readOutlookContext(office);
  if (!canOpenSavedOutlookDraft(office) || !ewsItemId)
    throw new Error(
      "Open this reply from Outlook’s Drafts folder. This Outlook client cannot open the saved draft directly.",
    );
  if (!expectedItemKey || current.itemKey !== expectedItemKey)
    throw new Error(
      "The selected email changed. Open Rivet on the original message before opening its draft.",
    );
  let nativeId = ewsItemId;
  if (idFormat === "rest") {
    if (!office!.context.mailbox!.convertToEwsId)
      throw new Error(
        "Use the saved Outlook link to open this draft. This Outlook client cannot convert its message identifier.",
      );
    nativeId = office!.context.mailbox!.convertToEwsId(ewsItemId, "v2.0");
    if (!nativeId)
      throw new Error(
        "Outlook could not resolve the saved draft. Open it from your Drafts folder.",
      );
  }
  await new Promise<void>((resolve, reject) => {
    office!.context.mailbox!.displayMessageFormAsync!(nativeId, (result) => {
      if (result.status === "succeeded") resolve();
      else
        reject(
          new Error(
            result.error?.message ||
              "Outlook could not open the saved reply. It is still in your Drafts folder.",
          ),
        );
    });
  });
}

export async function openOutlookReply(
  office: OfficeRuntime | undefined,
  expectedItemKey: string,
  body: string,
  attachments: ReplyFile[] = [],
): Promise<void> {
  const context = readOutlookContext(office);
  if (!context.canReply || !expectedItemKey)
    throw new Error(
      "Open this draft from a supported Outlook message to reply in its original thread.",
    );
  if (context.itemKey !== expectedItemKey)
    throw new Error(
      "The selected email changed. Open Rivet on the original message before inserting this reply.",
    );
  if (attachments.length && !canAttachOutlookFiles(office))
    throw new Error(
      "This Outlook client cannot insert the selected files. Review a reply without attachments or use a supported Outlook client.",
    );
  const html = replyHtml(body);
  if (new TextEncoder().encode(html).length > 32000)
    throw new Error(
      "This reply is too long for Outlook. Shorten it before opening the reply.",
    );
  const item = office!.context.mailbox!.item!;
  await new Promise<void>((resolve, reject) =>
    item.displayReplyFormAsync!(
      attachments.length
        ? {
            htmlBody: html,
            attachments: attachments.map((file) => ({
              type: "base64",
              name: file.name,
              base64file: file.base64,
              inLine: false,
            })),
          }
        : html,
      (result) => {
        if (result.status === "succeeded") resolve();
        else
          reject(
            new Error(
              result.error?.message ||
                "Outlook could not open the reply. Your reviewed draft is saved in Rivet.",
            ),
          );
      },
    ),
  );
}
