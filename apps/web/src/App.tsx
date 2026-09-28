import { useState, useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  SquaresFour,
  Stack,
  BookOpen,
  Plus,
  MagnifyingGlass,
  ArrowUpRight,
  ArrowRight,
  ArrowLeft,
  Command,
  FolderSimple,
  Clock,
  CheckCircle,
  Files,
  Sparkle,
  Lightning,
  SlidersHorizontal,
  Database,
  Info,
  GitDiff,
  Check,
  House,
  HardDrives,
  SidebarSimple,
} from "@phosphor-icons/react";
import { api, usd, dateLabel, when, type Project, type Workspace } from "./api";
import { Mark, Status, Modal, Empty, ErrorNote, Busy } from "./ui";
import { QuoteWorkspace } from "./Workspace";

type Health = {
  status: string;
  mode: string;
  assistant_configured: boolean;
  model: string;
  actor: string;
};
export default function App() {
  const qc = useQueryClient();
  const [route, setRoute] = useState(
    () => location.hash.slice(1) || "projects",
  );
  const [newProject, setNewProject] = useState(false);
  const [search, setSearch] = useState(false);
  const [toast, setToast] = useState("");
  const [mobileNav, setMobileNav] = useState(false);
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Project[]>("/projects"),
    refetchInterval: 5000,
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api<Health>("/health"),
    refetchInterval: 30000,
  });
  const navigate = useCallback((to: string) => {
    location.hash = to;
    setRoute(to);
    setMobileNav(false);
  }, []);
  const notify = useCallback((message: string) => setToast(message), []);
  useEffect(() => {
    const f = () => setRoute(location.hash.slice(1) || "projects");
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key === "k" &&
        !route.startsWith("project/")
      ) {
        e.preventDefault();
        setSearch(true);
      }
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [route]);
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(""), 4500);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  const recent = [...(projects.data ?? [])].sort((a, b) =>
    b.updated_at.localeCompare(a.updated_at),
  );
  const project = projects.data?.find(
    (p) => "project/" + p.id === route.split("?")[0],
  );
  return (
    <div className="app-shell">
      <aside className={"sidebar " + (mobileNav ? "mobile-open" : "")}>
        <button className="brand" onClick={() => navigate("projects")}>
          <Mark />
          <span>
            rivet<span className="brand-dot">.</span>
          </span>
        </button>
        <button
          className="workspace-switch"
          onClick={() => navigate("settings")}
        >
          <span className="workspace-icon">R</span>
          <div>
            Rivet workspace<small>Local · Personal</small>
          </div>
          <SlidersHorizontal size={16} />
        </button>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          <button
            className={
              route === "projects" || route.startsWith("project/")
                ? "active"
                : ""
            }
            onClick={() => navigate("projects")}
          >
            <SquaresFour size={18} />
            Projects
            <span className="nav-count">{projects.data?.length ?? 0}</span>
          </button>
          <button
            className={route === "catalog" ? "active" : ""}
            onClick={() => navigate("catalog")}
          >
            <Stack size={18} />
            Equipment catalog
          </button>
          <button
            className={route === "activity" ? "active" : ""}
            onClick={() => navigate("activity")}
          >
            <Clock size={18} />
            Activity
          </button>
        </nav>
        <div className="nav-label recent-label">
          RECENT PROJECTS
          <button aria-label="New project" onClick={() => setNewProject(true)}>
            <Plus size={14} />
          </button>
        </div>
        <div className="recent-projects">
          {recent.slice(0, 4).map((p) => (
            <button
              key={p.id}
              className={project?.id === p.id ? "selected" : ""}
              onClick={() => navigate("project/" + p.id)}
            >
              <span className={"project-dot " + p.color} />
              <span>{p.title}</span>
            </button>
          ))}
        </div>
        <div className="sidebar-bottom">
          <div className="demo-label">
            <span className="pulse-dot" />
            LOCAL PROTOTYPE
          </div>
          <p>
            Sample projects are synthetic.
            <br />
            Stored locally. AI uses selected sources.
          </p>
          <button onClick={() => navigate("guide")}>
            <BookOpen size={17} />
            Getting started
            <ArrowUpRight size={14} />
          </button>
          <button onClick={() => navigate("settings")}>
            <SlidersHorizontal size={17} />
            Workspace settings
          </button>
          <div className="profile">
            <span>LE</span>
            <div>
              Local estimator<small>Personal workspace</small>
            </div>
            <span className="online-dot" />
          </div>
        </div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div>
            <button
              className="icon-button mobile-menu"
              aria-label="Toggle navigation"
              onClick={() => setMobileNav(!mobileNav)}
            >
              <SidebarSimple size={20} />
            </button>
            <button className="breadcrumb" onClick={() => navigate("projects")}>
              Workspace
            </button>
            <span className="slash">/</span>
            <span>
              {project?.title ??
                {
                  projects: "Projects",
                  catalog: "Equipment catalog",
                  activity: "Activity",
                  settings: "Settings",
                  guide: "Getting started",
                }[route] ??
                "Projects"}
            </span>
          </div>
          <div className="topbar-right">
            <span className="connection">
              <span className={health.data ? "online-dot" : "offline-dot"} />
              {health.data ? "All changes saved locally" : "Connecting"}
            </span>
            <button
              className="search-trigger"
              aria-label="Search projects"
              onClick={() => setSearch(true)}
            >
              <MagnifyingGlass size={17} />
              <kbd>⌘ K</kbd>
            </button>
          </div>
        </header>
        {projects.isError ? (
          <div className="page">
            <ErrorNote message="Rivet could not reach its local server. Start the app using the instructions in the README, then refresh." />
            <button onClick={() => projects.refetch()}>Try again</button>
          </div>
        ) : projects.isLoading ? (
          <div className="page">
            <Busy text="Opening your workspace…" />
          </div>
        ) : route.startsWith("project/") ? (
          <QuoteWorkspace
            id={route.split("/")[1].split("?")[0]}
            notify={notify}
            assistantReady={health.data?.assistant_configured ?? false}
            onBack={() => navigate("projects")}
          />
        ) : route === "catalog" ? (
          <Catalog />
        ) : route === "activity" ? (
          <Activity projects={projects.data ?? []} navigate={navigate} />
        ) : route === "settings" ? (
          <Settings health={health.data} />
        ) : route === "guide" ? (
          <Guide onNew={() => setNewProject(true)} navigate={navigate} />
        ) : (
          <Projects
            projects={projects.data ?? []}
            navigate={navigate}
            onNew={() => setNewProject(true)}
          />
        )}
      </div>
      {newProject && (
        <NewProject
          onClose={() => setNewProject(false)}
          onCreated={(p) => {
            setNewProject(false);
            qc.invalidateQueries({ queryKey: ["projects"] });
            navigate("project/" + p.id);
            notify("Project created. Add your first source document.");
          }}
        />
      )}
      {search && (
        <Search
          projects={projects.data ?? []}
          onClose={() => setSearch(false)}
          navigate={(to) => {
            setSearch(false);
            navigate(to);
          }}
        />
      )}
      {toast && (
        <div className="toast" role="status">
          <CheckCircle weight="fill" size={20} />
          {toast}
        </div>
      )}
    </div>
  );
}

function Projects({
  projects,
  navigate,
  onNew,
}: {
  projects: Project[];
  navigate: (s: string) => void;
  onNew: () => void;
}) {
  const [filter, setFilter] = useState("All projects");
  const [query, setQuery] = useState("");
  const filtered = projects.filter(
    (p) =>
      (filter === "All projects" ||
        (filter === "Needs review" && (p.blockers > 0 || p.pending > 0)) ||
        (filter === "Approved" && p.status === "approved")) &&
      (p.title + " " + p.customer).toLowerCase().includes(query.toLowerCase()),
  );
  const total = projects.reduce((a, p) => a + Number(p.total), 0),
    pending = projects.reduce((a, p) => a + p.pending, 0),
    featured = projects.find((p) => p.pending > 0);
  return (
    <main className="page projects-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">YOUR QUOTING WORKSPACE</span>
          <h1>
            Projects<span className="muted-period">.</span>
          </h1>
          <p>Keep every quote, source, and revision connected.</p>
        </div>
        <button className="primary" onClick={onNew}>
          <Plus size={17} />
          New project
        </button>
      </div>
      <div className="overview-stats">
        <div>
          <span className="stat-label">ACTIVE PROJECTS</span>
          <strong>
            {String(
              projects.filter((p) => p.status !== "approved").length,
            ).padStart(2, "0")}
            <small>in your workspace</small>
          </strong>
        </div>
        <div>
          <span className="stat-label">
            QUOTED VALUE <span>USD</span>
          </span>
          <strong>
            {usd(total)}
            <small>across {projects.length} projects</small>
          </strong>
        </div>
        <div>
          <span className="stat-label">PENDING CHANGES</span>
          <strong>
            {String(pending).padStart(2, "0")}
            <small>
              {pending ? "waiting for your review" : "you’re up to date"}
            </small>
          </strong>
        </div>
      </div>
      {featured && (
        <button
          className="attention-banner"
          onClick={() => navigate("project/" + featured.id + "?changes")}
        >
          <div className="signal-icon">
            <GitDiff size={24} />
          </div>
          <div>
            <span className="eyebrow">A NEW REVISION. A CLEAR NEXT STEP.</span>
            <h3>{featured.title} has changes to review.</h3>
            <p>Compare the proposed edits with their original sources.</p>
          </div>
          <span className="attention-action">
            Review changes
            <ArrowUpRight size={19} />
          </span>
          <div className="signal-art" aria-hidden="true">
            <i />
            <i />
            <i />
            <i />
            <i />
          </div>
        </button>
      )}
      <div className="projects-layout">
        <section>
          <div className="list-toolbar">
            <div className="segmented">
              {["All projects", "Needs review", "Approved"].map((x) => (
                <button
                  key={x}
                  className={filter === x ? "selected" : ""}
                  onClick={() => setFilter(x)}
                >
                  {x}
                  {x === "All projects" && <span>{projects.length}</span>}
                </button>
              ))}
            </div>
            <label className="compact-search">
              <MagnifyingGlass size={16} />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Find a project"
                aria-label="Find a project"
              />
            </label>
          </div>
          <div className="project-table-head">
            <span>PROJECT / CUSTOMER</span>
            <span>QUOTE VALUE</span>
            <span>STATUS</span>
            <span>BID DUE</span>
            <span />
          </div>
          <div className="project-list">
            {filtered.map((p, i) => (
              <button
                className="project-row"
                key={p.id}
                onClick={() => navigate("project/" + p.id)}
              >
                <div className="project-name">
                  <span className={"folder-box " + p.color}>
                    <FolderSimple size={23} weight="duotone" />
                  </span>
                  <div>
                    <h3>{p.title}</h3>
                    <p>
                      {p.customer}
                      <span>·</span>
                      {p.number}
                    </p>
                  </div>
                </div>
                <div className="project-value">
                  {usd(p.total)}
                  <small>{p.lines} equipment lines</small>
                </div>
                <div>
                  <Status status={p.pending ? "pending" : p.status} />
                  <small className="rev-label">
                    Revision {String(p.version).padStart(2, "0")}
                  </small>
                </div>
                <div className="due-date">
                  {dateLabel(p.due_date)}
                  <small>{p.due_date?.slice(0, 4) ?? ""}</small>
                </div>
                <ArrowUpRight className="row-arrow" size={18} />
              </button>
            ))}
            {!filtered.length && (
              <Empty
                title="No projects here yet"
                icon={<FolderSimple size={30} />}
              >
                {query
                  ? "Try a different search."
                  : "Create a project to start building your first quote."}
              </Empty>
            )}
          </div>
          <div className="list-footer">
            <span>
              {filtered.length} project{filtered.length !== 1 ? "s" : ""}
            </span>
            <span>
              <Database size={13} />
              Saved to your local workspace
            </span>
          </div>
        </section>
        <aside className="workspace-note">
          <span className="eyebrow">BUILT AROUND THE DETAILS</span>
          <div className="connection-graphic" aria-hidden="true">
            <div className="graphic-top">
              <Files size={21} />
              <span>Source</span>
            </div>
            <div className="graphic-line" />
            <div className="graphic-core">
              <Mark small />
            </div>
            <div className="graphic-branches">
              <span>Quote</span>
              <span>Evidence</span>
            </div>
          </div>
          <h3>
            One quote.
            <br />
            Every source behind it.
          </h3>
          <p>
            Open a project to trace a value, resolve an exception, or review
            what changed.
          </p>
          <button className="text-button" onClick={() => navigate("guide")}>
            Explore the workflow
            <ArrowRight size={15} />
          </button>
        </aside>
      </div>
      <div className="sample-disclosure">
        <Info size={15} />
        <span>
          The three starter projects use fictional equipment, customers, offers,
          and prices. Create a project for your own work.
        </span>
      </div>
    </main>
  );
}
function NewProject({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (p: Project) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal title="Start a project" eyebrow="A NEW CONNECTION" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          setBusy(true);
          setError("");
          try {
            onCreated(
              await api<Project>("/projects", {
                title: f.get("title"),
                customer: f.get("customer"),
                due_date: f.get("due_date") || null,
                category: "Power distribution",
              }),
            );
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="modal-intro">
          Bring the bid package, equipment, and revisions into one place.
        </p>
        <label>
          Project name
          <input
            name="title"
            required
            minLength={2}
            maxLength={180}
            placeholder="e.g. Northline Research Campus"
            autoFocus
          />
        </label>
        <label>
          Customer
          <input
            name="customer"
            required
            minLength={2}
            maxLength={180}
            placeholder="Company or contractor"
          />
        </label>
        <div className="form-row">
          <label>
            Bid due date
            <input name="due_date" type="date" />
          </label>
          <label>
            Equipment category
            <input value="Power distribution" readOnly />
          </label>
        </div>
        {error && <ErrorNote message={error} />}
        <footer className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy}>
            {busy ? (
              <Busy />
            ) : (
              <>
                Create project
                <ArrowRight size={17} />
              </>
            )}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
function Search({
  projects,
  onClose,
  navigate,
}: {
  projects: Project[];
  onClose: () => void;
  navigate: (s: string) => void;
}) {
  const [q, setQ] = useState("");
  const matches = projects.filter((p) =>
    (p.title + " " + p.customer).toLowerCase().includes(q.toLowerCase()),
  );
  return (
    <Modal title="Find your next move" onClose={onClose}>
      <label className="global-search">
        <MagnifyingGlass size={20} />
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search projects…"
          aria-label="Search projects"
        />
      </label>
      <div className="search-results">
        {matches.map((p) => (
          <button key={p.id} onClick={() => navigate("project/" + p.id)}>
            <FolderSimple size={20} />
            <span>
              {p.title}
              <small>{p.customer}</small>
            </span>
            <ArrowUpRight size={17} />
          </button>
        ))}
        {!matches.length && (
          <Empty title="No matching projects">
            Try another name or customer.
          </Empty>
        )}
      </div>
    </Modal>
  );
}
function Catalog() {
  const [search, setSearch] = useState("");
  const { data, error } = useQuery({
    queryKey: ["catalog"],
    queryFn: () => api<any[]>("/catalog"),
  });
  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">YOUR APPROVED STARTING POINT</span>
          <h1>
            Equipment catalog<span className="muted-period">.</span>
          </h1>
          <p>Products and offers available for selection in your quotes.</p>
        </div>
      </div>
      <div className="info-banner">
        <Info size={18} />
        <p>
          Import your own catalog or offers from a project’s Sources tab.
          Confirm the column mapping before using the values.
        </p>
      </div>
      <label className="compact-search catalog-search">
        <MagnifyingGlass size={17} />
        <input
          placeholder="Search model or equipment"
          aria-label="Search equipment"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </label>
      {error && <ErrorNote message={(error as Error).message} />}
      <div className="catalog-grid">
        {data
          ?.filter((c) =>
            (c.model + " " + c.description)
              .toLowerCase()
              .includes(search.toLowerCase()),
          )
          .map((c) => (
            <article className="catalog-item" key={c.id}>
              <div className="catalog-item-top">
                <HardDrives size={27} weight="duotone" />
                {c.synthetic && <span className="micro-tag">SYNTHETIC</span>}
              </div>
              <span className="eyebrow">{c.manufacturer}</span>
              <h3>{c.description}</h3>
              <code>{c.model}</code>
              <dl>
                <div>
                  <dt>Supplier cost</dt>
                  <dd>
                    {usd(c.offers[0]?.cost)}{" "}
                    <small>/ {c.offers[0]?.unit ?? "—"}</small>
                  </dd>
                </div>
                <div>
                  <dt>Offer valid through</dt>
                  <dd>{dateLabel(c.offers[0]?.valid_until)}</dd>
                </div>
              </dl>
              <div className="catalog-lead">
                <Clock size={14} />
                {c.offers[0]?.lead_time ?? "No supplier offer attached"}
              </div>
            </article>
          ))}
      </div>
      {data?.length === 0 && (
        <Empty title="Your catalog starts here" icon={<Stack size={28} />}>
          Open a project and import a catalog spreadsheet in Sources.
        </Empty>
      )}
    </main>
  );
}
function Activity({
  projects,
  navigate,
}: {
  projects: Project[];
  navigate: (s: string) => void;
}) {
  const { data } = useQuery({
    queryKey: ["all-activity", projects.map((p) => p.id)],
    queryFn: async () => {
      const workspaces = await Promise.all(
        projects.map((p) => api<Workspace>("/projects/" + p.id)),
      );
      return workspaces
        .flatMap((w) => w.events.map((e) => ({ ...e, project: w.project })))
        .sort((a: any, b: any) => String(b.at).localeCompare(String(a.at)));
    },
  });
  return (
    <main className="page narrow-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">THE RECORD BEHIND THE WORK</span>
          <h1>
            Activity<span className="muted-period">.</span>
          </h1>
          <p>Every edit and decision, kept in order.</p>
        </div>
      </div>
      <div className="timeline">
        {data?.map((e: any) => (
          <button
            key={e.id}
            className="timeline-event"
            onClick={() => navigate("project/" + e.project.id)}
          >
            <span className="timeline-icon">
              <Clock size={17} />
            </span>
            <div>
              <h3>{e.summary}</h3>
              <p>
                {e.project.title} <span>·</span> {e.actor}
              </p>
            </div>
            <time>{when(e.at)}</time>
          </button>
        ))}
      </div>
    </main>
  );
}
function Settings({ health }: { health: Health | undefined }) {
  return (
    <main className="page narrow-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">WORKSPACE</span>
          <h1>
            Settings<span className="muted-period">.</span>
          </h1>
        </div>
      </div>
      <section className="settings-section">
        <h3>Connection & storage</h3>
        <div>
          <span>
            Workspace mode
            <small>Single-user prototype, accessible on localhost.</small>
          </span>
          <Status status="ready" />
        </div>
        <div>
          <span>
            Database
            <small>
              PostgreSQL · original files stored privately on this device.
            </small>
          </span>
          <span className="micro-tag">LOCAL</span>
        </div>
        <div>
          <span>
            AI assistant
            <small>
              {health?.assistant_configured
                ? health.model
                : "Set OPENAI_API_KEY and OPENAI_MODEL in the server configuration to enable."}
            </small>
          </span>
          <Status
            status={health?.assistant_configured ? "ready" : "unreviewed"}
          />
        </div>
      </section>
      <section className="settings-section">
        <h3>Commercial policy</h3>
        <div>
          <span>
            Quotation model<small>Buy / sell · one currency per quote.</small>
          </span>
          <strong>USD</strong>
        </div>
        <div>
          <span>
            Money & rounding
            <small>
              Exact decimal calculations. Unit prices and line totals round to
              cents.
            </small>
          </span>
          <span className="micro-tag">HALF UP</span>
        </div>
        <div>
          <span>
            Customer exports
            <small>
              Approval required. Internal costs and margins are excluded.
            </small>
          </span>
          <CheckCircle size={22} />
        </div>
      </section>
      <div className="info-banner">
        <Info size={18} />
        <p>
          This local prototype does not include shared-user sign-in, live
          supplier inventory, or external sending.
        </p>
      </div>
    </main>
  );
}
function Guide({
  onNew,
  navigate,
}: {
  onNew: () => void;
  navigate: (s: string) => void;
}) {
  return (
    <main className="page narrow-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">GETTING STARTED</span>
          <h1>
            From documents
            <br />
            to a reviewed quote.
          </h1>
          <p>A short path, with you in control at every step.</p>
        </div>
      </div>
      <div className="guide-steps">
        {[
          [
            "01",
            "Bring the sources",
            "Create a project. Upload a text-based PDF, CSV, XLSX, or text request. Confirm spreadsheet columns; unreadable pages stay visible.",
          ],
          [
            "02",
            "Build the draft",
            "Use a mapped schedule, add equipment manually, or ask the connected assistant. Check quantities, price units, costs, and product selections.",
          ],
          [
            "03",
            "Review the evidence",
            "Select any line to inspect its sources. Approve each line after reviewing its commercial values and delivery wording.",
          ],
          [
            "04",
            "Handle what changed",
            "Upload an addendum. Review before-and-after changes with citations before accepting them into a new revision.",
          ],
          [
            "05",
            "Approve & export",
            "Resolve outstanding checks and reconcile source revisions. Approve the exact revision, then download the customer PDF or spreadsheet.",
          ],
        ].map(([n, title, text]) => (
          <article key={n}>
            <span>{n}</span>
            <div>
              <h3>{title}</h3>
              <p>{text}</p>
            </div>
          </article>
        ))}
      </div>
      <button className="primary" onClick={onNew}>
        <Plus size={17} />
        Create your project
      </button>
    </main>
  );
}
