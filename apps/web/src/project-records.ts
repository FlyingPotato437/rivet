import type { RecordComment, RecordView } from "./record-types";

export function visibleProjectComments(
  w: RecordView,
  search: string,
  revision = "latest",
) {
  const latest = w.revision_review?.latest_revision;
  return w.comments.filter(
    (c) =>
      (revision === "all" ||
        !latest ||
        c.revision === (revision === "latest" ? latest : revision)) &&
      `${c.log_reference || ""} ${c.number} ${c.text} ${c.response} ${c.author}`
        .toLowerCase()
        .includes(search.trim().toLowerCase()),
  );
}

/** Saving a note must not approve engineering or claim an email was sent. */
export function responseNoteWrite(
  comment: RecordComment,
  response: string,
  version: number,
  actor: string,
) {
  return {
    expected_version: version,
    actor,
    reason: "Update the recorded comment response.",
    text: comment.text,
    number: comment.number,
    revision: comment.revision,
    author: comment.author,
    authored_at: comment.authored_at,
    status: comment.status,
    response,
    responder: actor,
    target_source_id: comment.target_source_id,
    linked_email_id: comment.linked_email_id,
    reviewed: comment.reviewed,
    workflow_status: comment.workflow_status,
  };
}
