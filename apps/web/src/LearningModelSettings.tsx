import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, when } from "./api";
import { useAccount } from "./Auth";
import { ErrorNote } from "./ui";
import "./learning-model.css";

export type LearningModel = {
  policy: { private_training: boolean; share_features: boolean };
  active: {
    id: string;
    scope: string;
    trained_at: string;
    report: Report;
  } | null;
  latest: {
    id: string;
    status: string;
    trained_at: string;
    report: Report;
  } | null;
};
type Report = {
  episodes: number;
  heldout_episodes?: number;
  candidate_brier?: number;
  baseline_brier?: number;
};

export function LearningModelSettings() {
  const admin = useAccount().role === "org:admin";
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["learning-model"],
    queryFn: () => api<LearningModel>("/company-context/learning/model"),
    enabled: admin,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!admin) return null;
  const d = q.data;
  async function change(
    kind: "private_training" | "share_features",
    value: boolean,
  ) {
    if (!d) return;
    setBusy(true);
    setError("");
    try {
      await api(
        "/company-context/learning/policy",
        {
          private_training: d.policy.private_training,
          share_features: d.policy.share_features,
          [kind]: value,
          reason: `Administrator ${value ? "enabled" : "disabled"} ${kind}.`,
        },
        "PUT",
      );
      await qc.invalidateQueries({ queryKey: ["learning-model"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function train() {
    setBusy(true);
    setError("");
    try {
      await api("/company-context/learning/train", {});
      await qc.invalidateQueries({ queryKey: ["learning-model"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function rollback() {
    if (!d?.active || d.active.scope !== "private") return;
    setBusy(true);
    setError("");
    try {
      await api(`/company-context/learning/models/${d.active.id}/rollback`, {});
      await qc.invalidateQueries({ queryKey: ["learning-model"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="learning-settings">
      <header>
        <div>
          <h3>Learning and improvement</h3>
          <p>
            Rivet learns which approved examples are most useful, using the
            replies your team actually sends.
          </p>
        </div>
        <span className="connection-label">
          {d?.active
            ? "Ranking model active"
            : d?.policy.private_training === false
              ? "Paused"
              : "Collecting examples"}
        </span>
      </header>
      {(error || q.isError) && (
        <ErrorNote message={error || (q.error as Error).message} />
      )}
      {q.isPending && <p role="status">Loading learning settings…</p>}
      {d && (
        <>
          <label className="learning-option">
            <span>
              <strong>Improve example selection automatically</strong>
              <small>
                Fit a private ranking model in the background. Releases must
                pass a held-out evaluation. Your source and approval rules still
                apply.
              </small>
            </span>
            <input
              type="checkbox"
              checked={d.policy.private_training}
              disabled={busy}
              onChange={(e) =>
                void change("private_training", e.target.checked)
              }
            />
          </label>
          <label className="learning-option">
            <span>
              <strong>Contribute numeric feedback to shared learning</strong>
              <small>
                Optional. Share only numeric overlap features and sent-unchanged
                versus edited labels. No email, document text, drawings or
                customer names enter the shared model. Turning this off
                invalidates contributed releases.
              </small>
            </span>
            <input
              type="checkbox"
              checked={d.policy.share_features}
              disabled={busy}
              onChange={(e) => void change("share_features", e.target.checked)}
            />
          </label>
          <div className="learning-training-state">
            <div>
              <strong>
                {d.active
                  ? "Validated example ranking is in use"
                  : "Your private memory is available now"}
              </strong>
              <p>
                {d.latest
                  ? `${d.latest.report.episodes} eligible learning episodes · ${d.latest.report.heldout_episodes || 0} held out · recorded ${when(d.latest.trained_at)}`
                  : "Training starts automatically as reviewed replies accumulate."}
              </p>
              <small>
                A minimum of 40 reply episodes across 6 customer/order groups is
                required. The measured outcome is editing effort, not
                engineering correctness. This trains Rivet’s example ranker, not
                the foundation language model.
              </small>
            </div>
            <button
              className="secondary"
              disabled={busy || !d.policy.private_training}
              onClick={() => void train()}
            >
              {busy ? "Checking…" : "Check for improvements"}
            </button>
          </div>
          {d.active?.scope === "private" && (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void rollback()}
            >
              Revert to standard example selection
            </button>
          )}
        </>
      )}
    </section>
  );
}
