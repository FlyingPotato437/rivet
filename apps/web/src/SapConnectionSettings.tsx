import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowSquareOut, ArrowsClockwise } from "@phosphor-icons/react";
import { api, when } from "./api";
import { useAccount } from "./Auth";
import { ErrorNote } from "./ui";
import "./learning-model.css";

type Profile = {
  key: string;
  order_id: string | null;
  order_number: string;
  label: string;
  environment: string;
  status: string;
  source_status: string;
  checked_at: string | null;
  documents: number;
  error: string;
  resources: { document_id: string; name: string; sha256: string }[];
};

export function SapConnectionSettings() {
  const admin = useAccount().role === "org:admin";
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["sap-connection"],
    queryFn: () => api<{ profiles: Profile[] }>("/integrations/sap"),
    enabled: admin,
    refetchInterval: 8000,
  });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  if (!admin) return null;
  async function check(p: Profile) {
    setBusy(p.key);
    setError("");
    try {
      await api("/integrations/sap/check", { order_id: p.order_id });
      await qc.invalidateQueries({ queryKey: ["sap-connection"] });
      await qc.invalidateQueries({ queryKey: ["adoption-readiness"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="learning-settings sap-connection">
      <header>
        <div>
          <h3>SAP order context</h3>
          <p>
            Read accepted scope from an explicitly mapped SAP sales order.
            Retrieved records become cited evidence in Rivet.
          </p>
        </div>
        <span className="connection-label">Read only</span>
      </header>
      {(error || q.isError) && (
        <ErrorNote message={error || (q.error as Error).message} />
      )}
      {q.isPending && <p role="status">Checking SAP connection…</p>}
      {q.data?.profiles.length === 0 && (
        <p className="connection-empty">
          Add an authorized SAP endpoint and order mapping on the server to
          connect a manufacturer.
        </p>
      )}
      {q.data?.profiles.map((p) => (
        <article className="sap-profile" key={`${p.order_number}:${p.key}`}>
          <div className="sap-profile-heading">
            <div>
              <span className="connection-overline">{p.environment}</span>
              <h4>{p.order_number}</h4>
            </div>
            <span
              className={`connection-label ${p.source_status === "current" && p.status === "current" ? "is-current" : ""}`}
            >
              {p.status === "pending"
                ? "Checking…"
                : p.status === "current" && p.source_status === "current"
                  ? "Verified"
                  : p.status === "stale"
                    ? "Ready to recheck"
                    : "Needs a check"}
            </span>
          </div>
          <p>
            {p.documents} retrieved {p.documents === 1 ? "record" : "records"}
            {p.checked_at ? ` · Last checked ${when(p.checked_at)}` : ""}
          </p>
          {p.environment === "SAP sandbox" && (
            <p className="sap-boundary">
              Live SAP API, generic sample order. This record is separate from
              customer equipment scope.
            </p>
          )}
          {p.error && <ErrorNote message={p.error} />}
          <div className="connection-actions">
            <button
              className="secondary"
              disabled={!!busy || !p.order_id || p.status === "pending"}
              onClick={() => void check(p)}
            >
              <ArrowsClockwise size={16} />
              {busy === p.key || p.status === "pending"
                ? "Checking SAP…"
                : "Check connection"}
            </button>
            {p.order_id && (
              <a
                className="connection-source-link"
                href={`#order/${p.order_id}?tab=files`}
              >
                View retrieved evidence <ArrowSquareOut size={15} />
              </a>
            )}
          </div>
        </article>
      ))}
    </section>
  );
}
