import {
  ArrowUpRight,
  FileText,
  LinkSimple,
  Clock,
  ChatText,
  WarningCircle,
} from "@phosphor-icons/react";
import type { RecordView } from "./record-types";
import { when } from "./api";
import { Attribution, ActivityAttribution } from "./RecordAttribution";

export function CommentConnections({
  id,
  w,
  openSource,
  openChanges,
}: {
  id: string;
  w: RecordView;
  openSource: (id: string) => void;
  openChanges: () => void;
}) {
  const c = w.comments.find((c) => c.id === id);
  const links = w.connections?.comments[id];
  if (!c || !links) return null;
  const source = w.sources.find((s) => s.id === links.source_ids[0]);
  const drawing = w.sources.find((s) => s.id === links.drawing_source_id);
  const doc = w.documents.find(
    (d) => d.id === (source?.document_id || links.document_id),
  );
  const events = w.events.filter((e) => links.event_ids.includes(e.id));
  const references =
    w.coordination?.links.filter(
      (link) =>
        link.from_type === "comment" &&
        link.from_id === id &&
        link.type === "references",
    ) ?? [];
  return (
    <section className="record-connections" aria-label="Connected records">
      <div className="record-connections-label">
        <LinkSimple size={13} /> Connected records
      </div>
      {references.length > 0 && (
        <details>
          <summary>
            <LinkSimple size={13} />
            Why these references are connected
          </summary>
          {references.map((link) => (
            <div className="record-anchor-reason" key={link.id}>
              <small>
                {link.automatic ? "Linked automatically" : "Recorded link"} ·{" "}
                {link.confidence === "explicit"
                  ? "Explicit reference"
                  : "Needs review"}
              </small>
              <p>{link.reason}</p>
              {link.source_ids.map((sourceId) => (
                <button
                  type="button"
                  className="text-button"
                  key={sourceId}
                  onClick={() => openSource(sourceId)}
                >
                  Source
                  {w.sources.find((s) => s.id === sourceId)?.location.page
                    ? ` · p. ${w.sources.find((s) => s.id === sourceId)?.location.page}`
                    : ""}
                  <ArrowUpRight size={12} />
                </button>
              ))}
            </div>
          ))}
        </details>
      )}
      <div className="record-connected-links">
        {doc && (
          <button
            type="button"
            className="text-button"
            onClick={() => openSource(source?.id || doc.id)}
          >
            <FileText size={14} />
            Original source{c.source_page ? ` · p. ${c.source_page}` : ""}
            <ArrowUpRight size={12} />
          </button>
        )}
        {drawing ? (
          <span className="record-linked-drawing">
            <button
              type="button"
              className="text-button"
              onClick={() => openSource(drawing.id)}
            >
              <LinkSimple size={14} />
              Linked drawing
              {drawing.location.page ? ` · p. ${drawing.location.page}` : ""}
              <ArrowUpRight size={12} />
            </button>
            <Attribution
              kind={
                references.some(
                  (link) => link.automatic && link.to_id === drawing.id,
                )
                  ? "automatic"
                  : "human"
              }
            >
              {references.some(
                (link) => link.automatic && link.to_id === drawing.id,
              )
                ? "Auto-linked"
                : "Linked by team"}
            </Attribution>
          </span>
        ) : (
          <span className="record-connection-unknown">
            Drawing location not linked
          </span>
        )}
        {links.email_document_id && (
          <button
            type="button"
            className="text-button"
            onClick={() => openSource(links.email_document_id)}
          >
            Linked email <ArrowUpRight size={12} />
          </button>
        )}
        {links.change_ids.length > 0 && (
          <button type="button" className="text-button" onClick={openChanges}>
            {links.change_ids.length} linked{" "}
            {links.change_ids.length === 1 ? "change" : "changes"}
            <ArrowUpRight size={12} />
          </button>
        )}
      </div>
      {w.connections.latest_approval_id && (
        <p className="record-connection-approval">
          {links.matches_approved_record
            ? "Matches the latest approved record."
            : links.in_approved_record
              ? "Updated since the latest record approval."
              : "Not included in the latest approved record."}
        </p>
      )}
      {events.length > 0 && (
        <details>
          <summary>
            <Clock size={13} />
            {events.length} recorded{" "}
            {events.length === 1 ? "update" : "updates"}
          </summary>
          {events.slice(0, 4).map((e) => (
            <div className="record-connection-event" key={e.id}>
              <strong>{e.summary}</strong>
              <ActivityAttribution actor={e.actor} kind={e.actor_kind} />
              <span>{when(e.at)}</span>
              <p>{e.reason}</p>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}

export function RecordContext({
  w,
  openSharing,
  openChanges,
}: {
  w: RecordView;
  openSharing: () => void;
  openChanges: () => void;
}) {
  const updates = w.connections?.after_approval;
  const changed = updates
    ? updates.comment_ids.length +
      updates.change_ids.length +
      updates.document_ids.length
    : 0;
  const missing = ["Customer", "Production"].filter(
    (team) => !w.subscribers.some((p) => p.team === team),
  );
  return (
    <div
      className="record-context"
      aria-label="Record and communication status"
    >
      <button type="button" onClick={changed ? openChanges : openSharing}>
        <WarningCircle size={14} />
        {changed
          ? `${changed} ${changed === 1 ? "update" : "updates"} since record approval`
          : w.approvals.length
            ? "Approved record available"
            : "No record approval yet"}
        <ArrowUpRight size={12} />
      </button>
      <button type="button" onClick={openSharing}>
        <ChatText size={14} />
        {missing.length
          ? `${missing.join(" & ")} recipients not set`
          : `${w.subscribers.length} notice recipients`}
        <ArrowUpRight size={12} />
      </button>
    </div>
  );
}
