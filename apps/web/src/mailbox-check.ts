/** A requested check is complete only after Microsoft folder checkpoints advance. */
export type WorkMailbox = {
  id: string;
  enabled: boolean;
  status: string;
  mailbox?: string;
  poll_interval_seconds?: number;
  folders: {
    id: string;
    state: string;
    last_synced_at: string | null;
    last_attempt_at?: string | null;
    error?: string;
  }[];
};

export type MailboxCheckTarget = {
  id: string;
  mailbox: string;
  folders: WorkMailbox["folders"];
};

export function mailboxCheckTargets(
  items: WorkMailbox[],
): MailboxCheckTarget[] {
  return items
    .filter((item) => item.enabled)
    .map((item) => ({
      id: item.id,
      mailbox: item.mailbox || "Outlook mailbox",
      folders: item.folders.map((folder) => ({ ...folder })),
    }));
}

export async function requestMailboxChecks(
  targets: MailboxCheckTarget[],
  request: (path: string, body: object) => Promise<unknown>,
) {
  const results = await Promise.allSettled(
    targets.map((target) =>
      request(`/mailboxes/${encodeURIComponent(target.id)}/reconcile`, {}),
    ),
  );
  return {
    scheduled: targets.filter(
      (_, index) => results[index].status === "fulfilled",
    ),
    errors: results.flatMap((result, index) =>
      result.status === "rejected"
        ? [
            `${targets[index].mailbox}: ${result.reason instanceof Error ? result.reason.message : "Could not request a check."}`,
          ]
        : [],
    ),
  };
}

export function mailboxCheckProgress(
  targets: MailboxCheckTarget[],
  current: WorkMailbox[],
) {
  const errors: string[] = [];
  let complete = 0;
  for (const target of targets) {
    const mailbox = current.find((item) => item.id === target.id);
    if (!mailbox?.enabled) {
      errors.push(`${target.mailbox}: monitoring is no longer enabled.`);
      continue;
    }
    if (!target.folders.length) {
      errors.push(`${target.mailbox}: no monitored folders are configured.`);
      continue;
    }
    // Inspect every folder; an unfinished first folder must not hide another's failure.
    const checked = target.folders
      .map((before) => {
        const folder = mailbox.folders.find((item) => item.id === before.id);
        if (!folder) {
          errors.push(
            `${target.mailbox}: the monitored folders changed. Start a new check.`,
          );
          return false;
        }
        if (
          folder.error &&
          folder.last_attempt_at &&
          folder.last_attempt_at !== before.last_attempt_at
        ) {
          errors.push(`${target.mailbox}: ${folder.error}`);
        }
        return (
          folder.state === "current" &&
          !!folder.last_synced_at &&
          (!before.last_synced_at ||
            Date.parse(folder.last_synced_at) >
              Date.parse(before.last_synced_at))
        );
      })
      .every(Boolean);
    if (checked) complete++;
  }
  return {
    complete: targets.length > 0 && complete === targets.length,
    errors: [...new Set(errors)],
  };
}

export function lastMailboxCheck(items: WorkMailbox[]) {
  const folders = items
    .filter((item) => item.enabled)
    .flatMap((item) => item.folders);
  if (!folders.length || folders.some((folder) => !folder.last_synced_at))
    return null;
  // The oldest folder checkpoint describes coverage of the whole monitored inbox.
  return folders
    .map((folder) => folder.last_synced_at!)
    .sort((a, b) => Date.parse(a) - Date.parse(b))[0];
}
