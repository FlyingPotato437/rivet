import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useAccount } from "./Auth";
import { ErrorNote } from "./ui";
import "./company-context.css";

type Rule = {
  id: string;
  title: string;
  text: string;
  status: string;
  version: number;
  evidence_current: boolean;
  provenance: { note?: string };
};
type Example = {
  id: string;
  order_id: string;
  outcome: string;
  input_text: string;
  original_text: string;
  human_response: string;
  reuse_status: string;
  reuse_scope: string;
  reuse_purpose: string;
  generation?: { reply_id?: string };
  evidence_current: boolean;
  topics: string[];
  factual_edit_withheld?: boolean;
};
type Overview = {
  rules: Rule[];
  feedback: Example[];
  learning_mode: string;
  metrics?: {
    reviewed_responses: number;
    accepted: number;
    edited: number;
    rejected: number;
    approved_current_examples: number;
  };
};

export default function CompanyContextSettings() {
  const account = useAccount();
  const admin = account.role === "org:admin";
  const data = useQuery({
    queryKey: ["company-context"],
    queryFn: () => api<Overview>("/company-context"),
    enabled: admin,
  });
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [instruction, setInstruction] = useState("");
  const [provenance, setProvenance] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function change(path: string, body: unknown, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(path, body);
      await data.refetch();
      setNotice(message);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!admin) return null;
  const active =
    data.data?.rules.filter(
      (r) => r.status === "approved" && r.evidence_current,
    ) || [];
  const examples =
    data.data?.feedback.filter(
      (f) =>
        f.reuse_status === "approved" &&
        f.reuse_purpose === "retrieval" &&
        !f.factual_edit_withheld &&
        f.evidence_current,
    ) || [];
  return (
    <section className="company-settings">
      <header>
        <div>
          <h3>Company guidance</h3>
          <p>
            Teach Rivet how your team works. You decide which instructions and
            reviewed responses it may reuse.
          </p>
        </div>
        <button className="secondary" onClick={() => setAdding(!adding)}>
          {adding ? "Cancel" : "Add instruction"}
        </button>
      </header>
      {error && <ErrorNote message={error} />}
      {data.isError && <ErrorNote message={(data.error as Error).message} />}
      {notice && (
        <p className="company-notice" role="status">
          {notice}
        </p>
      )}
      {data.isPending && <p role="status">Loading company guidance…</p>}
      {adding && (
        <form
          className="company-instruction-form"
          onSubmit={async (e) => {
            e.preventDefault();
            const saved = await change(
              "/company-context/rules",
              {
                key: "instruction-" + crypto.randomUUID(),
                title,
                text: instruction,
                provenance_note: provenance,
              },
              "Instruction saved for your review. Approve it below to enable reuse.",
            );
            if (saved) {
              setAdding(false);
              setTitle("");
              setInstruction("");
              setProvenance("");
            }
          }}
        >
          <label>
            Instruction name
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              minLength={2}
              maxLength={180}
              placeholder="For example, drawing reference checks"
            />
          </label>
          <label>
            What should Rivet do?
            <textarea
              rows={4}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              required
              minLength={3}
              maxLength={6000}
              placeholder="Ask for the sheet number and revision when a returned comment does not identify a drawing."
            />
          </label>
          <label>
            Where does this instruction come from?
            <input
              value={provenance}
              onChange={(e) => setProvenance(e.target.value)}
              required
              minLength={8}
              maxLength={2000}
              placeholder="Team procedure, reviewed with the project manager"
            />
          </label>
          <p>
            Instructions guide coordination and wording. They cannot approve
            engineering changes or override current source documents.
          </p>
          <button className="primary" disabled={busy}>
            {busy ? "Saving…" : "Save for review"}
          </button>
        </form>
      )}
      {data.data && (
        <>
          <div className="company-summary">
            <span>
              <strong>{active.length}</strong> active instructions
            </span>
            <span>
              <strong>
                {data.data.metrics?.approved_current_examples ??
                  examples.length}
              </strong>{" "}
              approved examples
            </span>
            <span>Private to {account.team}</span>
          </div>
          {data.data.rules.length === 0 && (
            <p className="company-empty">
              No company instructions yet. Start with one review or
              correspondence rule your team already follows.
            </p>
          )}
          <div className="company-guidance-list">
            {data.data.rules.map((rule) => (
              <details key={rule.id}>
                <summary>
                  <span>{rule.title}</span>
                  <small>
                    {!rule.evidence_current
                      ? "Evidence changed"
                      : rule.status === "draft"
                        ? "Needs approval"
                        : rule.status}
                  </small>
                </summary>
                <div className="company-detail">
                  <p>{rule.text}</p>
                  {rule.provenance.note && (
                    <p className="company-origin">
                      Source: {rule.provenance.note}
                    </p>
                  )}
                  {rule.status === "draft" && rule.evidence_current && (
                    <button
                      disabled={busy}
                      className="secondary"
                      onClick={() =>
                        change(
                          `/company-context/rules/${rule.id}/approve`,
                          {
                            reason:
                              "Administrator reviewed this instruction in company settings.",
                          },
                          "Instruction approved for future work.",
                        )
                      }
                    >
                      Approve instruction
                    </button>
                  )}
                  {rule.status === "approved" && (
                    <button
                      disabled={busy}
                      className="text-button"
                      onClick={() =>
                        change(
                          `/company-context/rules/${rule.id}/revoke`,
                          {
                            reason:
                              "Administrator withdrew this instruction in company settings.",
                          },
                          "Instruction removed from future context.",
                        )
                      }
                    >
                      Stop using this instruction
                    </button>
                  )}
                </div>
              </details>
            ))}
          </div>
          <details className="company-feedback">
            <summary>
              <span>Reviewed responses</span>
              <small>{data.data.feedback.length} recent reviews</small>
            </summary>
            <p>
              Review an Outlook draft to record an accepted, edited, or rejected
              response. Reuse needs approval; feedback alone does not train the
              model.
            </p>
            {!!data.data.metrics?.reviewed_responses && (
              <div className="company-review-outcomes">
                <div>
                  <span>
                    <strong>{data.data.metrics.accepted}</strong> accepted
                  </span>
                  <span>
                    <strong>{data.data.metrics.edited}</strong> edited
                  </span>
                  <span>
                    <strong>{data.data.metrics.rejected}</strong> rejected
                  </span>
                </div>
                <p>
                  Recorded review outcomes. Accuracy is measured separately on
                  held-out orders.
                </p>
              </div>
            )}
            {data.data.feedback.length === 0 && (
              <p className="company-empty">
                Your first reviewed reply will appear here.
              </p>
            )}
            {data.data.feedback.map((item) => (
              <details className="company-example" key={item.id}>
                <summary>
                  <span>
                    {item.outcome === "edited"
                      ? "Edited response"
                      : item.outcome === "accepted"
                        ? "Accepted response"
                        : "Rejected response"}
                  </span>
                  <small>
                    {item.factual_edit_withheld &&
                    item.reuse_purpose !== "evaluation"
                      ? "Factual edit · withheld from response examples"
                      : !item.evidence_current
                        ? "Evidence changed"
                        : item.reuse_status === "approved"
                          ? item.reuse_purpose === "evaluation"
                            ? "Reserved for quality checks"
                            : `Reusable for ${item.reuse_scope}`
                          : "Not reused"}
                  </small>
                </summary>
                <div className="company-detail">
                  <a href={`#order/${item.order_id}`}>Open source order</a>
                  <h4>Original draft</h4>
                  <p>{item.original_text}</p>
                  {item.human_response &&
                    item.human_response !== item.original_text && (
                      <>
                        <h4>Reviewed response</h4>
                        <p>{item.human_response}</p>
                      </>
                    )}
                  {item.reuse_status === "unapproved" &&
                    item.evidence_current &&
                    item.outcome !== "rejected" && (
                      <button
                        disabled={busy}
                        className="secondary"
                        onClick={() =>
                          change(
                            `/company-context/feedback/${item.id}/approve`,
                            {
                              reason:
                                "Administrator reviewed this response for use on similar company work.",
                              scope: item.topics.length ? "company" : "order",
                              purpose: "retrieval",
                            },
                            "Approved as an example. Current order evidence still takes priority.",
                          )
                        }
                      >
                        Approve as an example
                      </button>
                    )}
                  {item.reuse_status === "approved" && (
                    <button
                      disabled={busy}
                      className="text-button"
                      onClick={() =>
                        change(
                          `/company-context/feedback/${item.id}/revoke`,
                          {
                            reason:
                              "Administrator withdrew this response example from company context.",
                          },
                          "Example removed from future context.",
                        )
                      }
                    >
                      Stop reusing this example
                    </button>
                  )}
                  {item.reuse_status === "unapproved" &&
                    item.evidence_current &&
                    item.generation?.reply_id && (
                      <div>
                        <p>
                          Reserve this response for private quality checks. Its
                          order will be withheld from reusable examples and
                          customer history during the comparison.
                        </p>
                        <button
                          disabled={busy}
                          className="secondary"
                          onClick={() =>
                            change(
                              `/company-context/feedback/${item.id}/approve`,
                              {
                                reason:
                                  "Administrator reserved this reviewed response for private evaluation, separately from retrieval examples.",
                                scope: "order",
                                purpose: "evaluation",
                              },
                              "Reserved for quality checks. It will not be reused as a response example.",
                            )
                          }
                        >
                          Reserve for quality checks
                        </button>
                      </div>
                    )}
                </div>
              </details>
            ))}
          </details>
          <p className="company-footnote">
            Examples help with wording and procedure. Rivet checks the current
            order separately; historical prices, specifications, and approvals
            are never current evidence.
          </p>
        </>
      )}
    </section>
  );
}
