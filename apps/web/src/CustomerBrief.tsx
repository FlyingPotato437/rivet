import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import "./customer-brief.css";

type Note = {
  id: string;
  title: string;
  text: string;
  applicability_note: string;
  source_url: string;
  reviewed_by: string;
  provenance: { source_order_number: string };
};
type Brief = {
  version: number;
  customer: { id: string; name: string } | null;
  preferences: Note[];
  history: Note[];
  withheld: { id: string; title: string; reason: string }[];
  partial: boolean;
  snapshot: string;
  documents: { id: string; name: string; revision: string; state: string }[];
};
type Match = {
  source_url: string;
  excerpt: string;
  filename: string;
  location: { page?: number; line?: number; sheet?: string; cell?: string };
  order_number?: string;
  revision?: string;
};
type Check = {
  brief_snapshot: string;
  checks: { note: Note; status: string; matches: Match[] }[];
  partial: boolean;
  notice: string;
  withheld_count: number;
};
function Source({
  match,
  href,
}: {
  match: Match;
  href: (hash: string) => string;
}) {
  return (
    <div className="cb-excerpt">
      <blockquote>{match.excerpt}</blockquote>
      <a href={href(match.source_url)} target="_blank" rel="noreferrer">
        {[
          match.order_number,
          match.filename,
          match.revision && `Rev ${match.revision}`,
          match.location.page && `p. ${match.location.page}`,
          match.location.line && `line ${match.location.line}`,
          match.location.sheet,
          match.location.cell,
        ]
          .filter(Boolean)
          .join(" · ")}
      </a>
    </div>
  );
}
export function CustomerBrief({
  orderId,
  workspace = "",
  initiallyOpen = false,
  hideWhenUnlinked = false,
}: {
  orderId: string;
  workspace?: string;
  initiallyOpen?: boolean;
  hideWhenUnlinked?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const [view, setView] = useState<"brief" | "check" | "search">("brief");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [documents, setDocuments] = useState<string[]>([]);
  const [checked, setChecked] = useState<{
    data: Check;
    snapshot: string;
  } | null>(null);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<{
    query: string;
    results: Match[];
    has_more: boolean;
  } | null>(null);
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["customer-brief", orderId],
    queryFn: () =>
      api<Brief>(`/company-context/orders/${orderId}/customer-brief`),
    staleTime: 0,
    refetchOnWindowFocus: true,
  });
  const customers = useQuery({
    queryKey: ["customers"],
    queryFn: () =>
      api<{ customers: { id: string; name: string }[] }>(
        "/company-context/customers",
      ),
    enabled: open && !!query.data && !query.data.customer,
  });
  const href = (hash: string) =>
    workspace ? `${workspace.split("#")[0]}${hash}` : hash;
  const b = query.data;
  const validCheck =
    checked && checked.snapshot === b?.snapshot ? checked.data : null;
  async function link(customerId: string) {
    if (!b || !customerId) return;
    setBusy(true);
    setError("");
    try {
      await api(
        `/company-context/orders/${orderId}/customer`,
        {
          customer_id: customerId,
          expected_version: b.version,
          reason:
            "PM explicitly selected this customer for the order briefing.",
        },
        "PUT",
      );
      await query.refetch();
      void qc.invalidateQueries({ queryKey: ["customers"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (hideWhenUnlinked && !b?.customer) return null;
  return (
    <details
      className="customer-brief"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>
        <span>Customer history</span>
        <small>
          {b?.customer
            ? `${b.customer.name} · ${b.preferences.length} preferences`
            : query.isPending
              ? "Loading…"
              : "Link customer history"}
        </small>
      </summary>
      <div className="cb-body">
        {(error || query.isError) && (
          <p className="cb-warning" role="alert">
            {error || (query.error as Error).message}
          </p>
        )}
        {b && !b.customer && (
          <div className="cb-empty">
            <h3>Bring past requests into this order</h3>
            <p>
              Choose the confirmed customer. Rivet keeps their reviewed history
              available when a new engineer takes over.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void link(
                  String(new FormData(e.currentTarget).get("customer")),
                );
              }}
            >
              <label>
                Customer
                <select name="customer" required defaultValue="">
                  <option value="" disabled>
                    Choose a customer
                  </option>
                  {customers.data?.customers.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <button type="submit" disabled={busy}>
                {busy ? "Linking…" : "Use this customer"}
              </button>
            </form>
            {customers.isError && (
              <p role="alert">
                Could not load customers. Try again from Customers.
              </p>
            )}
            <a
              href={href("#customers")}
              target={workspace ? "_blank" : undefined}
            >
              Add a customer or import past orders
            </a>
          </div>
        )}
        {b?.customer && (
          <>
            <p className="cb-intro">
              Reviewed requests from this customer’s previous jobs, with links
              to the original records.
            </p>
            <nav className="cb-tabs" aria-label="Customer history views">
              {(
                [
                  ["brief", "At kickoff"],
                  ["check", "Before sending"],
                  ["search", "Past orders"],
                ] as const
              ).map(([key, label]) => (
                <button
                  type="button"
                  key={key}
                  aria-pressed={view === key}
                  onClick={() => {
                    setView(key);
                    setError("");
                  }}
                >
                  {label}
                </button>
              ))}
            </nav>
            {b.partial && (
              <p className="cb-warning">
                This brief shows a limited set of records. Review the full
                customer history before relying on coverage.
              </p>
            )}
            {view === "brief" && (
              <>
                <p className="cb-intro">
                  Approved preferences to discuss before preparing the drawings.
                  Check their applicability to this job.
                </p>
                {b.preferences.length ? (
                  b.preferences.map((n) => (
                    <article className="cb-note" key={n.id}>
                      <h3>{n.title}</h3>
                      <p>{n.text}</p>
                      <p className="cb-condition">{n.applicability_note}</p>
                      <a
                        href={href(n.source_url)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Original request · {n.provenance.source_order_number}
                      </a>
                    </article>
                  ))
                ) : (
                  <p className="cb-empty">
                    No standing preferences approved yet. Historical requests
                    stay as references until your team confirms they apply
                    across jobs.
                  </p>
                )}
                {b.history.length > 0 && (
                  <details className="cb-history">
                    <summary>
                      {b.history.length} reviewed historical{" "}
                      {b.history.length === 1 ? "reference" : "references"}
                    </summary>
                    {b.history.map((n) => (
                      <article className="cb-note" key={n.id}>
                        <h3>{n.title}</h3>
                        <p>{n.text}</p>
                        <a
                          href={href(n.source_url)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          View original · {n.provenance.source_order_number}
                        </a>
                      </article>
                    ))}
                  </details>
                )}
              </>
            )}
            {view === "check" && (
              <>
                <p className="cb-intro">
                  Select the drawing revision you plan to send. Rivet finds
                  passages related to approved preferences for you to inspect.
                </p>
                <form
                  onSubmit={async (e) => {
                    e.preventDefault();
                    setBusy(true);
                    setError("");
                    setChecked(null);
                    try {
                      const data = await api<Check>(
                        `/company-context/orders/${orderId}/customer-check`,
                        { document_ids: documents },
                      );
                      setChecked({
                        data,
                        snapshot: data.brief_snapshot,
                      });
                      await query.refetch();
                    } catch (err) {
                      setError((err as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  <fieldset className="cb-documents">
                    <legend>Documents to inspect</legend>
                    {b.documents.map((d) => (
                      <label key={d.id}>
                        <input
                          type="checkbox"
                          checked={documents.includes(d.id)}
                          onChange={(e) => {
                            setChecked(null);
                            setDocuments(
                              e.target.checked
                                ? [...documents, d.id]
                                : documents.filter((id) => id !== d.id),
                            );
                          }}
                        />
                        <span>
                          {d.name}
                          {d.revision && <small>Rev {d.revision}</small>}
                        </span>
                      </label>
                    ))}
                    {!b.documents.length && (
                      <p>Add the outgoing drawings to this order first.</p>
                    )}
                  </fieldset>
                  <button
                    type="submit"
                    disabled={
                      busy || !documents.length || !b.preferences.length
                    }
                  >
                    {busy ? "Finding passages…" : "Check customer preferences"}
                  </button>
                  {!b.preferences.length && (
                    <p className="cb-condition">
                      Approve a standing preference before running this check.
                    </p>
                  )}
                </form>
                {checked && !validCheck && (
                  <p className="cb-warning">
                    Documents or customer context changed. Run the check again.
                  </p>
                )}
                {validCheck && (
                  <div aria-live="polite">
                    <p className="cb-condition">{validCheck.notice}</p>
                    {validCheck.partial && (
                      <p className="cb-warning">
                        Some selected content is unreadable, processing, or
                        outside the retrieval limit. This check is incomplete.
                      </p>
                    )}
                    {validCheck.checks.map((c) => (
                      <article className="cb-note" key={c.note.id}>
                        <span
                          className={
                            c.status === "passages_found"
                              ? "cb-status"
                              : "cb-warning"
                          }
                        >
                          {c.status === "passages_found"
                            ? "Related passages · inspect wording"
                            : "Not found in extracted text · inspect drawing"}
                        </span>
                        <h3>{c.note.title}</h3>
                        <p>{c.note.text}</p>
                        {c.matches.map((m) => (
                          <Source key={m.source_url} match={m} href={href} />
                        ))}
                        <a
                          href={href(c.note.source_url)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Compare with original customer request
                        </a>
                      </article>
                    ))}
                  </div>
                )}
              </>
            )}
            {view === "search" && (
              <>
                <p className="cb-intro">
                  Find the original answer, drawing note, or approval in this
                  customer’s linked orders.
                </p>
                <form
                  className="cb-search"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    setBusy(true);
                    setError("");
                    setResults(null);
                    try {
                      setResults(
                        await api(
                          `/company-context/customers/${b.customer!.id}/search`,
                          { query: search },
                        ),
                      );
                    } catch (err) {
                      setError((err as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  <label>
                    Search customer documents
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Cable entry, E-10, breaker…"
                      minLength={2}
                      maxLength={400}
                      required
                    />
                  </label>
                  <button type="submit" disabled={busy}>
                    {busy ? "Searching…" : "Find sources"}
                  </button>
                </form>
                {results && (
                  <div aria-live="polite">
                    <p className="cb-condition">
                      {results.results.length} source matches for “
                      {results.query}”. Historical records may describe
                      different revisions.
                    </p>
                    {results.results.map((m, i) => (
                      <Source key={i} match={m} href={href} />
                    ))}
                    {!results.results.length && (
                      <p className="cb-empty">
                        No text matches. Try a drawing number or fewer words.
                        Scanned content may not be searchable.
                      </p>
                    )}
                    {results.has_more && (
                      <p className="cb-condition">
                        More matches exist. Narrow your search to see the
                        relevant records.
                      </p>
                    )}
                  </div>
                )}
              </>
            )}
            {b.withheld.length > 0 && (
              <details className="cb-history">
                <summary>
                  {b.withheld.length} notes withheld from the brief
                </summary>
                <ul>
                  {b.withheld.map((n) => (
                    <li key={n.id}>
                      <strong>{n.title}</strong>
                      <span>{n.reason}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <footer>
              <a
                href={href(`#customer/${b.customer.id}`)}
                target={workspace ? "_blank" : undefined}
              >
                Manage customer history
              </a>
              <span>History assembled from reviewed records</span>
            </footer>
          </>
        )}
      </div>
    </details>
  );
}
