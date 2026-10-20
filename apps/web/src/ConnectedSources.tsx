import { useEffect, useState } from "react";
import { CheckCircle, CircleNotch, WarningCircle } from "@phosphor-icons/react";
import { api } from "./api";
import "./connected-sources.css";

export type ConnectedContext = {
  status: string;
  checked_at?: string;
  error?: string;
  systems: {
    key: string;
    label: string;
    kind: string;
    mode: string;
    status: string;
    documents: number;
    error?: string;
  }[];
  resources: {
    document_id: string;
    name: string;
    label: string;
    kind: string;
    mode: string;
    revision: string;
    lifecycle: string;
    source_ids: string[];
  }[];
  findings: {
    kind: string;
    title: string;
    detail: string;
    document_ids: string[];
  }[];
};

export function ConnectedSources({
  orderId,
  snapshot,
  compact = false,
  workspace = window.location.origin,
}: {
  orderId: string;
  snapshot?: ConnectedContext | null;
  compact?: boolean;
  workspace?: string;
}) {
  const [live, setLive] = useState<ConnectedContext | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!orderId || snapshot) return;
    let disposed = false;
    async function load() {
      try {
        const next = await api<ConnectedContext>(
          `/orders/${encodeURIComponent(orderId)}/connected-context`,
        );
        if (!disposed) {
          setLive(next);
          setError("");
        }
      } catch {
        if (!disposed) setError("Could not check connected sources.");
      }
    }
    setLive(null);
    void load();
    const timer = window.setInterval(() => void load(), 10000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [orderId, snapshot]);
  const data = snapshot || live;
  if (!data || data.status === "not_configured")
    return error ? <p className="connected-error">{error}</p> : null;
  const current = data.status === "current";
  const working = ["pending", "syncing"].includes(data.status);
  const Icon = working
    ? CircleNotch
    : current && !data.findings.length
      ? CheckCircle
      : WarningCircle;
  const local = data.systems.some((s) => s.mode === "local_demo");
  const connected = data.systems.some((s) => s.mode === "live");
  const sandbox = data.systems.some((s) => /\bsandbox\b/i.test(s.label));
  function sourceHref(source: string) {
    const url = new URL(workspace);
    url.hash = `order/${encodeURIComponent(orderId)}?tab=work&source=${encodeURIComponent(source)}`;
    return url.href;
  }
  const content = (
    <>
      <div className="connected-heading">
        <Icon size={20} className={working ? "is-working" : ""} />
        <div>
          <strong>
            {current
              ? "Order context checked"
              : working
                ? "Gathering order context"
                : "Source check needs attention"}
          </strong>
          <span>
            {local && connected
              ? "Live and local sources · demo order"
              : local
                ? "Local integration demo · fictional records"
                : sandbox
                  ? "SAP sandbox · sample records"
                  : "Connected company sources"}
            {snapshot ? " · Draft snapshot" : ""}
          </span>
        </div>
      </div>
      <ol className="connected-system-list">
        {data.systems.map((system) => (
          <li key={system.key}>
            <span className={`connected-dot ${system.status}`} />
            <span className="connected-system-name">
              {system.label}
              <em>
                {system.mode === "local_demo"
                  ? "Local demo"
                  : /\bSAP\b/.test(system.label)
                    ? "Live · SAP API"
                    : "Live connection"}
              </em>
            </span>
            <small>
              {system.status === "current"
                ? `${system.documents} ${system.documents === 1 ? "file" : "files"}`
                : system.status.replaceAll("_", " ")}
            </small>
          </li>
        ))}
      </ol>
      {(data.error || error) && (
        <p className="connected-error">{data.error || error}</p>
      )}
      {!!data.findings.length && (
        <div className="connected-findings">
          {data.findings.map((finding, i) => (
            <details key={i}>
              <summary>
                <WarningCircle size={16} />
                <span>
                  {finding.kind === "source_conflict"
                    ? "Sources disagree: "
                    : ""}
                  {finding.title}
                </span>
              </summary>
              <p>{finding.detail}</p>
            </details>
          ))}
        </div>
      )}
      {!!data.resources.length && (
        <details className="connected-evidence">
          <summary>
            View retrieved evidence <span>{data.resources.length}</span>
          </summary>
          <ul>
            {data.resources.map((file) => (
              <li key={file.document_id}>
                <a
                  href={sourceHref(file.source_ids[0] || "")}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {file.name}
                </a>
                <span>
                  {file.label} ·{" "}
                  {file.revision ? `Rev ${file.revision} · ` : ""}
                  {file.lifecycle}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
  return (
    <section
      className={`connected-sources${compact ? " connected-sources-compact" : ""}`}
      aria-label="Connected source checks"
    >
      {compact ? (
        <details>
          <summary>
            <Icon size={18} />
            <span>
              {working
                ? "Gathering connected sources"
                : current
                  ? "Connected sources"
                  : "Source check needs attention"}
            </span>
            <small>
              {data.findings.length
                ? `${data.findings.length} ${data.findings.length === 1 ? "check" : "checks"} to review`
                : `${data.resources.length} ${data.resources.length === 1 ? "file" : "files"}`}
            </small>
          </summary>
          <div className="connected-expanded">{content}</div>
        </details>
      ) : (
        content
      )}
    </section>
  );
}
