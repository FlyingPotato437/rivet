import type {
  CommentImpact,
  CommentImpactKind,
  CommentImpactSummary,
} from "./record-types";
import { StatusIcon } from "./StatusIcon";

const labels: Record<CommentImpactKind, string> = {
  change: "Change",
  question: "Question",
  scope: "Adds scope",
  no_action: "No action",
  review: "Needs reading",
};

const money = (value: string, currency = "USD") =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(Number(value));

/** Automatic type for one comment, with an engineer marker when it applies. */
export function CommentTypeChip({ impact }: { impact?: CommentImpact }) {
  if (!impact) return null;
  return (
    <span className="comment-type">
      <span className={`comment-type-chip is-${impact.kind}`}>
        {labels[impact.kind]}
        {impact.kind === "scope" && impact.cost?.status === "estimated"
          ? ` · ~${money(impact.cost.estimate, impact.cost.currency)}`
          : ""}
      </span>
      {impact.engineer && (
        <span className="comment-type-chip is-engineer">Engineer</span>
      )}
    </span>
  );
}

/** Why the comment got its label, and the cost basis for added scope. */
export function CommentImpactDetail({ impact }: { impact?: CommentImpact }) {
  if (!impact) return null;
  const cost = impact.cost;
  return (
    <div className="comment-impact-detail">
      <span className="comment-impact-head">
        <CommentTypeChip impact={impact} />
        <small>Automatic · {impact.reasons.join(" · ")}</small>
      </span>
      {impact.kind === "scope" &&
        (cost?.status === "estimated" ? (
          <p>
            About {money(cost.estimate, cost.currency)} ({cost.quantity} ×{" "}
            {cost.tag} at {money(cost.unit_price, cost.currency)}), from{" "}
            {cost.source}. Billable {cost.billable}. Confirm before quoting.
          </p>
        ) : (
          <p>
            Adds scope. No trusted ERP price matches this item yet, so it needs
            pricing.
          </p>
        ))}
      {impact.engineer && (
        <p>An engineer decides this one before any response goes out.</p>
      )}
    </div>
  );
}

/** Which comments add scope, roughly what they are worth, and what needs an engineer. */
export function ChangeOrderPanel({
  summary,
  openComment,
}: {
  summary?: CommentImpactSummary;
  openComment: (id: string) => void;
}) {
  if (!summary) return null;
  const total = Object.values(summary.counts).reduce((a, b) => a + b, 0);
  if (!total) return null;
  const seen = new Set<string>();
  const scope = summary.scope.filter((s) => {
    const key = `${s.number}|${s.text.trim()}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return (
    <section className="change-order-panel" aria-label="Comment review">
      <header>
        <span className="needs-you-cap">
          <StatusIcon kind={scope.length ? "need" : "done"} size={13} />
          Comment review · automatic
        </span>
        {scope.length ? (
          <h2>
            {scope.length === 1 ? "Comment" : "Comments"}{" "}
            {scope.map((s) => s.number).join(", ")}{" "}
            {scope.length === 1 ? "adds" : "add"} scope
            {summary.estimated_total && summary.currency && (
              <span>
                {" "}
                · about {money(summary.estimated_total, summary.currency)}
              </span>
            )}
            {summary.needs_pricing > 0 && (
              <small className="change-order-unpriced">
                {" "}
                + {summary.needs_pricing} to price
              </small>
            )}
          </h2>
        ) : (
          <h2>No comment adds scope</h2>
        )}
        <div className="comment-counts">
          <span>
            <b className="mono">{summary.counts.change}</b>{" "}
            {summary.counts.change === 1 ? "change" : "changes"}
          </span>
          <span>
            <b className="mono">{summary.counts.question}</b>{" "}
            {summary.counts.question === 1 ? "question" : "questions"}
          </span>
          <span>
            <b className="mono">{summary.counts.scope}</b> add scope
          </span>
          <span>
            <b className="mono">{summary.engineer.length}</b>{" "}
            {summary.engineer.length === 1 ? "needs" : "need"} an engineer
          </span>
          {summary.counts.review > 0 && (
            <span>
              <b className="mono">{summary.counts.review}</b> need reading
            </span>
          )}
        </div>
      </header>
      {scope.length > 0 && (
        <ol>
          {scope.map((s) => (
            <li key={s.id}>
              <button onClick={() => openComment(s.id)}>
                <span className="mono change-order-number">{s.number}</span>
                <span className="change-order-text">{s.text}</span>
                <span className="change-order-cost">
                  {s.cost.status === "estimated" ? (
                    <>
                      <b>~{money(s.cost.estimate, s.cost.currency)}</b>
                      <small>
                        {s.cost.quantity} × {s.cost.tag} ·{" "}
                        {s.cost.billable === "per accepted scope"
                          ? "billable per accepted scope"
                          : "billability unconfirmed"}
                      </small>
                    </>
                  ) : (
                    <>
                      <b className="is-unpriced">Needs pricing</b>
                      <small>No trusted ERP price yet</small>
                    </>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
      <p className="change-order-note">
        Labels come from the comment text; prices only from trusted ERP rows.
        Confirm scope and billability before quoting.
      </p>
    </section>
  );
}
