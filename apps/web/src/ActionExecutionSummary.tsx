import { useQuery } from "@tanstack/react-query";
import { Check, Clock, WarningCircle } from "@phosphor-icons/react";
import { api, when } from "./api";
import { RivetIcon } from "./ui";
import {
  actionExecutionRows,
  type ActionExecutionView,
} from "./action-execution";
import "./action-execution.css";

export function ActionExecutionSummary({ orderId }: { orderId: string }) {
  const query = useQuery({
    queryKey: ["workflow-actions", orderId],
    queryFn: () =>
      api<ActionExecutionView>(`/orders/${orderId}/workflow-actions`),
    refetchInterval: 12000,
    retry: 1,
  });
  if (!query.data?.attempts.length || query.data.mode !== "local_http_demo")
    return null;
  const rows = actionExecutionRows(query.data.attempts);
  if (!rows.length) return null;
  const completed = rows.filter((row) => row.applied).length;
  const outstanding = rows.length - completed;
  return (
    <section
      className="action-execution-summary"
      aria-label="Recorded enterprise actions"
    >
      <header>
        <div className="action-execution-title">
          <RivetIcon name="agent" size={24} />
          <div>
            <span className="lifecycle-eyebrow">Rivet’s work</span>
            <h3>Work across connected systems</h3>
          </div>
        </div>
        <span className="lifecycle-mode">Local HTTP sandbox</span>
      </header>
      <p className="action-execution-intro">
        {completed} {completed === 1 ? "action has" : "actions have"} a saved
        execution receipt
        {outstanding
          ? ` · ${outstanding} ${outstanding === 1 ? "needs" : "need"} attention`
          : ""}
        . These changes ran in the local integration sandbox.
      </p>
      <div className="action-execution-list">
        {rows.map((row) => (
          <div
            className={`action-execution-row ${row.applied ? "applied" : row.waiting ? "waiting" : "blocked"}`}
            key={row.action}
          >
            <span className="action-execution-symbol" aria-hidden="true">
              {row.applied ? (
                <Check size={17} />
              ) : row.waiting ? (
                <Clock size={17} />
              ) : (
                <WarningCircle size={17} />
              )}
            </span>
            <div className="action-execution-result">
              <strong>{row.label}</strong>
              <span>
                {row.system}
                {row.applied
                  ? ` · record v${row.attempt.receipt!.remote_version}`
                  : row.waiting
                    ? " · receipt not confirmed"
                    : " · blocked"}
              </span>
              {!row.applied && row.attempt.message && (
                <p>{row.attempt.message}</p>
              )}
              {row.applied && !!row.attempt.receipt?.files?.length && (
                <p>
                  {row.attempt.receipt.files
                    .map((file) => file.name)
                    .join(" · ")}
                </p>
              )}
            </div>
            <time dateTime={row.attempt.receipt?.applied_at || row.attempt.at}>
              {when(row.attempt.receipt?.applied_at || row.attempt.at)}
            </time>
          </div>
        ))}
      </div>
      <details className="action-execution-evidence">
        <summary>Details</summary>
        {rows.map((row) => (
          <div className="action-execution-receipt" key={row.action}>
            <strong>
              {row.system} · {row.label}
            </strong>
            <span>
              {row.attempt.reconciled
                ? "Existing effect verified again; no duplicate created."
                : row.applied
                  ? "Applied and acknowledged by the local service."
                  : "No completed receipt is established."}
            </span>
            <code title={row.attempt.action_id}>
              Action {row.attempt.action_id}
            </code>
            {row.applied && (
              <>
                <code>
                  Approved package {row.attempt.receipt!.package_fingerprint}
                </code>
                <code>
                  Source snapshot {row.attempt.receipt!.source_state_sha256}
                </code>
                {row.attempt.receipt!.files.map((file) => (
                  <code key={file.document_id}>
                    {file.name} · {file.bytes.toLocaleString()} bytes · SHA-256{" "}
                    {file.sha256}
                  </code>
                ))}
              </>
            )}
          </div>
        ))}
      </details>
      {query.isError && (
        <p className="action-execution-refresh">
          Could not refresh the receipts. Showing the last saved result.
        </p>
      )}
      <p className="action-execution-footnote">
        No production vendor system was changed. Material availability is
        separate from a committed customer ship date.
      </p>
    </section>
  );
}
