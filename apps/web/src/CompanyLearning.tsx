import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  CaretDown,
  Check,
  ArrowsClockwise,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { useAccount } from "./Auth";
import "./company-learning.css";
import type { LearningModel } from "./LearningModelSettings";

type Episode = {
  id: string;
  order_id: string;
  order_number: string;
  outcome: string;
  before: string;
  after: string;
  reason: string;
  reuse_scope: string;
  evidence_current: boolean;
  available_for_reuse: boolean;
  requires_factual_review: boolean;
  correction?: {
    changes: { before: string; after: string }[];
    partial: boolean;
  } | null;
  selected_in_replies: number;
  evidence_count: number;
  at: string;
};
type Learning = {
  partial: boolean;
  window_limit: number;
  counts: {
    reviewed: number;
    corrected: number;
    rejected: number;
    reusable_examples: number;
    customer_preferences: number;
    company_rules: number;
    replies_with_memory: number;
    evaluation_cases: number;
    evaluation_orders: number;
  };
  episodes: Episode[];
  recent_uses: {
    reply_id: string;
    order_id: string;
    order_number: string;
    subject: string;
    selected: { examples: number; rules: number; customer_notes: number };
  }[];
  recurring_topics: {
    category: string;
    topics: string[];
    independent_orders: number;
  }[];
};

/** Administration history for response reuse; project work stays on the dashboard. */
export function CompanyLearning({
  navigate,
}: {
  navigate: (to: string) => void;
}) {
  const admin = useAccount().role === "org:admin";
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ["company-learning"],
    queryFn: () => api<Learning>("/company-context/learning"),
    enabled: admin,
    refetchInterval: 20000,
  });
  const model = useQuery({
    queryKey: ["learning-model"],
    queryFn: () => api<LearningModel>("/company-context/learning/model"),
    enabled: admin,
    refetchInterval: 30000,
  });
  if (!admin) return null;
  const d = query.data;
  const c = d?.counts;
  return (
    <section
      className="company-learning"
      aria-label="Response memory"
      id="response-memory"
    >
      <header>
        <div>
          <h2>Response memory</h2>
          <p>
            Review saved replies, corrections and examples available for reuse.
          </p>
        </div>
        <button
          className="learning-disclosure"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {open ? "Hide history" : "View history"}
          <CaretDown size={15} />
        </button>
      </header>
      {query.isError ? (
        <p className="learning-message" role="status">
          Learning history is unavailable.{" "}
          <button onClick={() => void query.refetch()}>Try again</button>
        </p>
      ) : (
        <div className="learning-summary" aria-label="Company learning summary">
          <dl>
            <div>
              <dt>Reviewed replies</dt>
              <dd>{c?.reviewed ?? "—"}</dd>
            </div>
            <div>
              <dt>Approved memory</dt>
              <dd>
                {c
                  ? c.reusable_examples +
                    c.customer_preferences +
                    c.company_rules
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>Replies using memory</dt>
              <dd>{c?.replies_with_memory ?? "—"}</dd>
            </div>
          </dl>
          <p>
            <ArrowsClockwise size={15} />
            {model.data?.active
              ? "Validated example ranking is active"
              : model.data?.policy.private_training === false
                ? "Automatic model learning is paused"
                : "Approved examples are available for future replies."}
          </p>
        </div>
      )}
      {open && d && (
        <div className="learning-evidence">
          <div className="learning-evidence-title">
            <h3>Corrections and reuse history</h3>
            <span>Observed history · not an accuracy score</span>
          </div>
          {!d.episodes.length && (
            <p className="learning-message">
              When a Rivet reply is sent or reviewed, its outcome appears here.
              Edited replies preserve the original and final wording.
            </p>
          )}
          {d.episodes.slice(0, 3).map((episode) => (
            <article className="learning-episode" key={episode.id}>
              <header>
                <button onClick={() => navigate(`order/${episode.order_id}`)}>
                  {episode.order_number}
                </button>
                <time dateTime={episode.at}>{when(episode.at)}</time>
              </header>
              {episode.outcome === "edited" ? (
                <>
                  <div
                    className="learning-deltas"
                    aria-label="Recorded wording changes"
                  >
                    {episode.correction?.changes
                      .slice(0, 3)
                      .map((change, index) => (
                        <div key={index}>
                          <span>{change.before || "Added wording"}</span>
                          <ArrowRight size={16} />
                          <strong>{change.after || "Removed"}</strong>
                        </div>
                      ))}
                    {!!episode.correction?.changes.length &&
                      episode.correction.changes.length > 3 && (
                        <small>More changes in the full comparison</small>
                      )}
                  </div>
                  <details className="learning-full-comparison">
                    <summary>Read full reply comparison</summary>
                    <div className="learning-comparison">
                      <div>
                        <span>Rivet’s draft</span>
                        <p>{episode.before}</p>
                      </div>
                      <div>
                        <span>Sent / reviewed wording</span>
                        <p>{episode.after}</p>
                      </div>
                    </div>
                  </details>
                </>
              ) : (
                <p>
                  {episode.outcome === "rejected"
                    ? "Response rejected. Retained as a failure case, excluded from positive examples."
                    : "Response accepted. Its observed acceptance is recorded separately from technical correctness."}
                </p>
              )}
              <footer>
                <span>{episode.evidence_count} evidence references</span>
                <span>
                  {episode.requires_factual_review
                    ? "Factual edit · withheld from reuse"
                    : !episode.evidence_current
                      ? "Sources changed · withheld"
                      : episode.available_for_reuse
                        ? `Available on ${episode.reuse_scope === "company" ? "relevant company jobs" : "this order"}`
                        : "Broader reuse needs review"}
                </span>
                <span>{episode.selected_in_replies} later reply inputs</span>
              </footer>
            </article>
          ))}
          {!!d.recent_uses.length && (
            <div className="learning-reuse-list">
              <h3>Memory selected for real replies</h3>
              {d.recent_uses.slice(0, 3).map((use) => (
                <button
                  key={use.reply_id}
                  onClick={() => navigate(`order/${use.order_id}`)}
                >
                  <Check size={17} />
                  <span>
                    <strong>{use.subject || use.order_number}</strong>
                    <small>
                      {use.selected.examples} reviewed examples ·{" "}
                      {use.selected.customer_notes} customer notes ·{" "}
                      {use.selected.rules} company rules
                    </small>
                  </span>
                  <ArrowRight size={16} />
                </button>
              ))}
            </div>
          )}
          <div className="learning-release-note">
            <strong>Test before wider use</strong>
            <p>
              Compare memory on and off against the same evidence. Withhold
              whole customers and jobs, have a person grade the answers, and
              block a release if it regresses. At least 10 cases across 3 jobs
              are required for release review.
            </p>
            <span>
              Example ranking trains in the background and uses only approved,
              current history. Shared numeric learning is optional in Workspace
              settings. Foundation language-model weights are unchanged.
            </span>
          </div>
          {d.partial && (
            <p className="learning-message">
              Recent sample of up to {d.window_limit} records per type; these
              are not lifetime totals.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export type LearningContext = {
  context_hash: string;
  ranking_model?: { id: string; scope: string } | null;
  examples: {
    id: string;
    source_order_id?: string;
    scope: string;
    corrected: boolean;
  }[];
  rules: { id: string; title: string }[];
  preferences: { id: string; title: string; source_order_id?: string }[];
  systems: { label: string; mode: string; status: string; documents: number }[];
};

/** Compact provenance of the exact reply. Does not trigger preparation or learning. */
export function ReplyLearning({ context }: { context?: LearningContext }) {
  if (
    !context ||
    !(
      context.examples.length +
      context.rules.length +
      context.preferences.length
    )
  )
    return null;
  return (
    <details className="reply-learning">
      <summary>
        <ArrowsClockwise size={16} />
        <span>Company memory used in this reply</span>
        <small>
          {context.examples.length} examples · {context.preferences.length}{" "}
          customer notes
        </small>
      </summary>
      <div>
        <p>
          Selected from your private, reviewed history and included in this
          reply’s saved input.
        </p>
        {context.preferences.map((p) => (
          <p key={p.id}>{p.title}</p>
        ))}
        {context.rules.map((r) => (
          <p key={r.id}>{r.title}</p>
        ))}
        <p>
          {context.examples.filter((e) => e.corrected).length} examples include
          a person’s recorded correction. Historical wording guides the reply;
          current records determine the facts.
        </p>
        <small>
          Selection is recorded. Its effect on answer quality has not been
          measured.
        </small>
      </div>
    </details>
  );
}
