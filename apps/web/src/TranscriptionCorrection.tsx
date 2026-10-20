import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { useAccount } from "./Auth";
import type { RecordComment, RecordView } from "./record-types";

export function TranscriptionCorrection({
  w,
  comment,
  onEditingChange,
}: {
  w: RecordView;
  comment: RecordComment;
  onEditingChange?: (editing: boolean) => void;
}) {
  const account = useAccount();
  const query = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(comment.text);
  const [version, setVersion] = useState(w.version);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    onEditingChange?.(editing);
    return () => onEditingChange?.(false);
  }, [editing, onEditingChange]);
  const scanned =
    comment.origin === "ai_transcription" ||
    comment.origin === "unreadable area" ||
    comment.confidence.toLowerCase().includes("transcription") ||
    comment.flags.some((flag) => /transcri|handwrit/i.test(flag));
  if (!scanned || !comment.document_id || !comment.source_ids.length) return null;
  if (!editing)
    return (
      <button
        type="button"
        className="text-button"
        onClick={() => {
          setText(comment.text);
          setVersion(w.version);
          setError("");
          setEditing(true);
        }}
      >
        {comment.transcription_reviewed ? "Edit corrected text" : "Correct text"}
      </button>
    );
  return (
    <form
      aria-label={`Correct transcription for comment ${comment.number}`}
      className="record-edit-metadata"
      onSubmit={async (event) => {
        event.preventDefault();
        if (busy) return;
        setBusy(true);
        setError("");
        try {
          const saved = await api<RecordView>(
            `/orders/${w.order.id}/record/comments/${comment.id}/transcription`,
            {
              expected_version: version,
              actor: account.name || "Project manager",
              reason: "Corrected transcription after checking the original page",
              text,
            },
          );
          query.setQueryData(["record", w.order.id], saved);
          void query.invalidateQueries({ queryKey: ["record-agent", w.order.id] });
          void query.invalidateQueries({ queryKey: ["project-work", w.order.id] });
          void query.invalidateQueries({ queryKey: ["order-reply-preparation", w.order.id] });
          setEditing(false);
        } catch (cause) {
          setError((cause as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <label>
        Text on the original page
        <textarea
          autoFocus
          required
          rows={3}
          maxLength={16000}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <small>The original stays saved. Your correction becomes a labelled example.</small>
      <div className="record-save-actions">
        <button
          className="primary"
          disabled={busy || !text.trim() || text.trim() === comment.text.trim()}
        >
          {busy ? "Saving…" : "Save correction"}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => setEditing(false)}
        >
          Cancel
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
