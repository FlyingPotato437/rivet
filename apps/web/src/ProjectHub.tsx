import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  ArrowDownLeft,
  Check,
  CircleNotch,
  Clock,
  EnvelopeSimple,
  FileText,
  FolderSimple,
  MagnifyingGlass,
  Plus,
  PencilSimple,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { useRecordOrders } from "./RecordFeed";
import { RecordIntake, IntakeConfirmation, type Intake } from "./RecordIntake";
import { CheckInboxNow } from "./YourWork";
import type { WorkMailbox } from "./mailbox-check";
import { ErrorNote } from "./ui";
import {
  projectFilters,
  projectMatches,
  type ProjectFlow,
} from "./project-flow";
import "./project-hub.css";

export function useProjectWork() {
  return useQuery({
    queryKey: ["project-work"],
    queryFn: () =>
      api<{ projects: ProjectFlow[]; partial: boolean }>("/project-work"),
    refetchInterval: 5000,
  });
}

export function ProjectStatus({ project }: { project: ProjectFlow }) {
  const Icon =
    project.status === "handling"
      ? CircleNotch
      : project.status === "waiting_customer"
        ? Clock
        : project.status === "ready" || project.status === "prepared"
          ? EnvelopeSimple
          : project.status === "needs_you"
            ? PencilSimple
            : Check;
  return (
    <span className={`project-flow-status is-${project.status}`}>
      <Icon
        size={15}
        className={project.status === "handling" ? "project-spinning" : ""}
      />
      {project.status_label}
    </span>
  );
}

export function ProjectNavigation({
  navigate,
  activeId,
}: {
  navigate: (to: string) => void;
  activeId?: string;
}) {
  const q = useProjectWork();
  return q.data?.projects.length ? (
    <div className="project-recent">
      <span>Recent orders</span>
      {q.data.projects.slice(0, 4).map((p) => (
        <button
          key={p.id}
          title={p.name}
          className={p.id === activeId ? "active" : ""}
          onClick={() => navigate(`order/${p.id}`)}
        >
          <span className={`project-state-dot is-${p.status}`} />
          <span>{p.name}</span>
        </button>
      ))}
    </div>
  ) : null;
}

export function ProjectsDashboard({
  navigate,
  onNew,
  view = "orders",
}: {
  navigate: (to: string) => void;
  onNew: () => void;
  view?: "today" | "orders";
}) {
  const q = useProjectWork(),
    orders = useRecordOrders(),
    qc = useQueryClient();
  const mailbox = useQuery({
    queryKey: ["work-mailboxes"],
    queryFn: () => api<{ items: WorkMailbox[] }>("/mailboxes"),
    refetchInterval: 30000,
  });
  const pending = useQuery({
    queryKey: ["record-intake", "pending"],
    queryFn: () =>
      api<{ items: Intake[] }>("/records/intake?status=needs_confirmation"),
    refetchInterval: 15000,
  });
  const [search, setSearch] = useState(""),
    [filter, setFilter] = useState("all"),
    [upload, setUpload] = useState(false);
  const connected = mailbox.data?.items.some((m) => m.enabled);
  const projects = q.data?.projects || [],
    visible = projects.filter(
      (p) =>
        projectMatches(p, search, filter) &&
        (view === "orders" ||
          ["ready", "prepared", "needs_you", "handling"].includes(p.status)),
    );
  const ready = projects.filter((p) =>
      ["ready", "prepared"].includes(p.status),
    ).length,
    working = projects.filter((p) => p.status === "handling").length,
    waiting = projects.filter((p) => p.status === "waiting_customer").length;
  const confirm = (item: Intake) => {
    void qc.invalidateQueries({ queryKey: ["record-intake"] });
    void qc.invalidateQueries({ queryKey: ["project-work"] });
  };
  return (
    <main className="page projects-hub">
      <header className="project-page-header">
        <div>
          <span className="project-eyebrow">Workspace</span>
          <h1>{view === "today" ? "Today" : "Orders"}</h1>
          <p>
            {view === "today"
              ? "Prepared replies and the few decisions that need you."
              : "Every customer conversation, file and outcome, together in its order."}
          </p>
        </div>
        <button className="secondary" onClick={onNew}>
          <Plus size={17} />
          New order
        </button>
      </header>
      <div className="project-mailbox-bar">
        <span className={`project-live-dot${connected ? " connected" : ""}`} />
        <span>
          {connected
            ? "Outlook connected · Rivet works in the background"
            : "Connect Outlook to capture order mail automatically"}
        </span>
        <div>
          {connected && <CheckInboxNow mailboxes={mailbox.data?.items || []} />}
          <button className="text-button" onClick={() => navigate("settings")}>
            {connected ? "Connection settings" : "Connect Outlook"}
          </button>
        </div>
      </div>
      {!!projects.length && (
        <p className="project-pulse">
          <strong>{ready}</strong> ready <span /> <strong>{working}</strong> in
          progress <span /> <strong>{waiting}</strong> waiting for customer
        </p>
      )}
      {!!pending.data?.items.length && (
        <section className="project-questions">
          <h2>One quick question</h2>
          {pending.data.items.slice(0, 3).map((item) => (
            <IntakeConfirmation
              key={item.intake_id}
              item={item}
              orders={orders.data?.orders || []}
              confirmed={confirm}
            />
          ))}
        </section>
      )}
      <div className="project-hub-controls">
        <div className="project-filters" aria-label="Filter orders">
          {projectFilters
            .filter((f) => view === "orders" || f.key !== "waiting_customer")
            .map((f) => (
              <button
                key={f.key}
                aria-pressed={filter === f.key}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
              </button>
            ))}
        </div>
        <label className="project-search">
          <MagnifyingGlass size={17} />
          <input
            aria-label="Search orders"
            placeholder="Find an order or email…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
      </div>
      {q.isError && <ErrorNote message={(q.error as Error).message} />}
      {q.isPending ? (
        <div className="project-skeleton" aria-label="Loading orders">
          <div />
          <div />
          <div />
        </div>
      ) : (
        <section
          className="project-list"
          aria-label="Projects and communication status"
        >
          <div className="project-table-heading">
            <span>Order</span>
            <span>Progress</span>
            <span>Last communication</span>
            <span />
          </div>
          {visible.map((p) => (
            <article className="project-hub-row" key={p.id}>
              <button
                className="project-hub-row-name"
                onClick={() => navigate(`order/${p.id}`)}
              >
                <span className="project-folder">
                  <FolderSimple size={22} />
                </span>
                <span>
                  <strong>{p.name}</strong>
                  <small>
                    {p.customer || "Customer not linked"} <span>·</span>{" "}
                    {p.number}
                  </small>
                  <span className="project-hub-row-counts">
                    {p.email_count} {p.email_count === 1 ? "email" : "emails"} ·{" "}
                    {p.document_count}{" "}
                    {p.document_count === 1 ? "file" : "files"}
                  </span>
                </span>
              </button>
              <div className="project-hub-row-progress">
                <ProjectStatus project={p} />
                {p.status === "handling" ? (
                  <div
                    className="project-mini-progress"
                    aria-label="Preparation progress"
                  >
                    {p.progress.map((step, i) => (
                      <span
                        key={i}
                        title={`${step.label}: ${step.state}`}
                        className={step.state}
                      />
                    ))}
                  </div>
                ) : (
                  <small>
                    {p.status === "waiting_customer"
                      ? "Rivet will pick up their reply"
                      : p.status === "needs_you"
                        ? "Open the order for the question"
                        : p.status === "ready"
                          ? "Draft and evidence prepared"
                          : p.status === "prepared"
                            ? "Source-linked response available"
                            : "Monitoring order updates"}
                  </small>
                )}
              </div>
              <div className="project-hub-row-communication">
                {p.last_communication ? (
                  <>
                    <span>
                      {p.last_communication.direction === "sent" ? (
                        <ArrowUpRight size={14} />
                      ) : (
                        <ArrowDownLeft size={14} />
                      )}{" "}
                      {p.last_communication.direction === "sent"
                        ? "Sent"
                        : "Received"}{" "}
                      <time dateTime={p.last_communication.at}>
                        {when(p.last_communication.at)}
                      </time>
                    </span>
                    <p title={p.last_communication.subject}>
                      {p.last_communication.subject}
                    </p>
                  </>
                ) : (
                  <>
                    <span>No email yet</span>
                    <p>Files and order context are available</p>
                  </>
                )}
              </div>
              <button
                className="project-open"
                aria-label={`Open order ${p.name}`}
                onClick={() => navigate(`order/${p.id}`)}
              >
                <ArrowUpRight size={18} />
              </button>
            </article>
          ))}
          {!visible.length && (
            <div className="project-empty">
              <FolderSimple size={32} />
              <h2>
                {projects.length
                  ? view === "today" && !search && filter === "all"
                    ? "Nothing needs you right now"
                    : "No matching orders"
                  : "Your orders start with an email"}
              </h2>
              <p>
                {projects.length
                  ? view === "today" && !search && filter === "all"
                    ? "Rivet keeps watching for updates. Open Orders for the full record."
                    : "Try another order name, job number or filter."
                  : "Connect Outlook once. Rivet files relevant mail by job, prepares the response and follows the conversation."}
              </p>
              <button
                className="secondary"
                onClick={
                  projects.length
                    ? () => {
                        setSearch("");
                        setFilter("all");
                      }
                    : onNew
                }
              >
                {projects.length ? "Clear filters" : "Create an order"}
              </button>
            </div>
          )}
        </section>
      )}
      <footer className="project-list-footer">
        <span>
          {projects.length} orders
          {q.data?.partial ? " · Recent 200 shown" : ""}
        </span>
        <div className="project-footer-actions">
          <button className="text-button" onClick={() => navigate("settings")}>
            Response memory settings
          </button>
          <button className="text-button" onClick={() => setUpload(!upload)}>
            {upload ? "Close upload" : "Add documents without email"}
          </button>
        </div>
      </footer>
      {upload && (
        <section className="project-upload">
          <RecordIntake
            navigate={navigate}
            orders={orders.data?.orders || []}
          />
        </section>
      )}
    </main>
  );
}

export { OrderStory as ProjectWorkspace } from "./OrderStory";
