import { TeamSwitcher, AccountControl, TeamSettings, useAccount } from "./Auth";
import { IntegrationSettings } from "./Integrations";
import { useState, useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Folder,
  ListChecks,
  CaretRight,
  ListMagnifyingGlass,
  DownloadSimple,
  FileCsv,
  Stack,
  BookOpen,
  Plus,
  MagnifyingGlass,
  ArrowUpRight,
  ArrowRight,
  FolderSimple,
  Clock,
  CheckCircle,
  SlidersHorizontal,
  Info,
  GitDiff,
  SidebarSimple,
} from "@phosphor-icons/react";
import { api, usd, dateLabel, when, type Project, type Workspace } from "./api";
import { Mark, Status, Modal, Empty, ErrorNote, Busy } from "./ui";
import { QuoteWorkspace } from "./Workspace";
import { WorkQueue } from "./WorkQueue";
import { NewOrder, OrderSearch } from "./OrderFeed";
import { RecordWorkspace as OrderWorkspace } from "./RecordWorkspace";
import {
  RecordFeed as OrderFeed,
  useRecordOrders as useOrders,
} from "./RecordFeed";
import { OrderGuide } from "./OrderGuide";
import sampleScheduleUrl from "../../../examples/equipment-schedule.csv?url";
import sampleAddendumUrl from "../../../examples/addendum-02.csv?url";
import switchgearQuoteUrl from "../../../examples/switchgear-current-quote.csv?url";
import switchgearOfferUrl from "../../../examples/switchgear-initial-offer.txt?url";
import switchgearAddendumUrl from "../../../examples/switchgear-addendum-02.txt?url";
import switchgearReplyUrl from "../../../examples/switchgear-revised-offer.txt?url";

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
  const [newOrder, setNewOrder] = useState(false);
  const orders = useOrders();
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
      if (e.key === "Escape") setMobileNav(false);
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key === "k" &&
        !route.startsWith("project/") &&
        !route.startsWith("order/")
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
  useEffect(() => {
    if (!mobileNav) return;
    const mobile = window.matchMedia("(max-width: 760px)");
    const closeOnDesktop = () => {
      if (!mobile.matches) setMobileNav(false);
    };
    mobile.addEventListener("change", closeOnDesktop);
    const previous = document.activeElement as HTMLElement | null;
    const nav = document.getElementById("workspace-navigation");
    const getItems = () =>
      [
        ...(nav?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ??
          []),
      ].filter((el) => el.offsetParent !== null);
    getItems()[0]?.focus();
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const items = getItems();
      if (event.shiftKey && document.activeElement === items[0]) {
        event.preventDefault();
        items.at(-1)?.focus();
      } else if (!event.shiftKey && document.activeElement === items.at(-1)) {
        event.preventDefault();
        items[0]?.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => {
      mobile.removeEventListener("change", closeOnDesktop);
      document.removeEventListener("keydown", trap);
      document.body.style.overflow = before;
      if (previous?.isConnected) previous.focus();
    };
  }, [mobileNav]);
  const recent = [...(orders.data?.orders ?? [])].sort((a, b) =>
    b.updated_at.localeCompare(a.updated_at),
  );
  const project = projects.data?.find(
    (p) => "project/" + p.id === route.split("?")[0],
  );
  const order = orders.data?.orders.find(
    (o) => "order/" + o.id === route.split("?")[0],
  );
  const decisions =
    orders.data?.orders.reduce((n, o) => n + o.counts.review, 0) ?? 0;
  return (
    <div className="app-shell">
      <aside
        id="workspace-navigation"
        className={"sidebar " + (mobileNav ? "mobile-open" : "")}
      >
        <button
          className="brand"
          aria-label="Rivet home"
          onClick={() => navigate("home")}
        >
          <Mark />
          <span>
            rivet<span className="brand-dot">.</span>
          </span>
        </button>
        <TeamSwitcher />
        <div className="nav-label">Workspace</div>
        <nav>
          <button
            className={
              route === "projects" ||
              route === "orders" ||
              route.startsWith("order/")
                ? "active"
                : ""
            }
            onClick={() => navigate("projects")}
          >
            <Folder size={18} />
            Orders
            <span className="nav-count">{orders.data?.orders.length ?? 0}</span>
          </button>
          <button
            className={route === "decisions" ? "active" : ""}
            onClick={() => navigate("decisions")}
          >
            <ListChecks size={18} />
            Needs review
            {decisions > 0 && <span className="nav-count">{decisions}</span>}
          </button>
        </nav>
        <div className="nav-label recent-label">
          Recent orders
          <button aria-label="New order" onClick={() => setNewOrder(true)}>
            <Plus size={14} />
          </button>
        </div>
        <div className="recent-projects">
          {recent.slice(0, 4).map((o) => (
            <button
              key={o.id}
              className={order?.id === o.id ? "selected" : ""}
              onClick={() => navigate("order/" + o.id)}
            >
              <span className="project-dot violet" />
              <span>{o.title}</span>
            </button>
          ))}
        </div>
        <details className="order-tools">
          <summary>
            Quote tools
            <CaretRight size={13} />
          </summary>
          <nav>
            <button
              className={
                route === "quotes" || route.startsWith("project/")
                  ? "active"
                  : ""
              }
              onClick={() => navigate("quotes")}
            >
              <Folder size={17} />
              Quotes
            </button>
            <button
              className={route === "bids" ? "active" : ""}
              onClick={() => navigate("bids")}
            >
              <ListChecks size={17} />
              Bid queue
            </button>
            <button
              className={route === "catalog" ? "active" : ""}
              onClick={() => navigate("catalog")}
            >
              <Stack size={17} />
              Equipment catalog
            </button>
            <button
              className={route === "activity" ? "active" : ""}
              onClick={() => navigate("activity")}
            >
              <Clock size={17} />
              Quote activity
            </button>
          </nav>
        </details>
        <div className="sidebar-bottom">
          <button onClick={() => navigate("guide")}>
            <BookOpen size={17} />
            Getting started
            <ArrowUpRight size={14} />
          </button>
          <button onClick={() => navigate("settings")}>
            <SlidersHorizontal size={17} />
            Workspace settings
          </button>
          <AccountControl />
        </div>
      </aside>
      {mobileNav && (
        <button
          className="nav-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileNav(false)}
        />
      )}
      <div className="app-main">
        <header className="topbar">
          <div>
            <button
              className="icon-button mobile-menu"
              aria-label="Toggle navigation"
              aria-expanded={mobileNav}
              aria-controls="workspace-navigation"
              onClick={() => setMobileNav(!mobileNav)}
            >
              <SidebarSimple size={20} />
            </button>
            <button className="breadcrumb" onClick={() => navigate("projects")}>
              Workspace
            </button>
            <span className="slash">/</span>
            <span>
              {order?.title ??
                project?.title ??
                {
                  projects: "Orders",
                  orders: "Orders",
                  decisions: "Needs review",
                  bids: "Bid queue",
                  quotes: "Quotes",
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
              <span
                className={
                  health.data && !health.isError ? "online-dot" : "offline-dot"
                }
              />
              {health.isError
                ? "Server unavailable"
                : health.data
                  ? "Local workspace"
                  : "Connecting…"}
            </span>
            <button
              className="search-trigger"
              aria-label="Search orders"
              onClick={() => setSearch(true)}
            >
              <MagnifyingGlass size={17} />
              {!route.startsWith("project/") && !route.startsWith("order/") && (
                <kbd>⌘ K</kbd>
              )}
            </button>
          </div>
        </header>
        {route.startsWith("order/") ? (
          <OrderWorkspace
            key={route.split("?")[0]}
            id={route.split("/")[1].split("?")[0]}
            navigate={navigate}
            notify={notify}
          />
        ) : ["projects", "orders", "decisions"].includes(route) ? (
          <OrderFeed
            key={route}
            navigate={navigate}
            onNew={() => setNewOrder(true)}
            decisionsOnly={route === "decisions"}
          />
        ) : projects.isError ? (
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
            onBack={() => navigate("quotes")}
          />
        ) : route === "catalog" ? (
          <Catalog />
        ) : route === "activity" ? (
          <Activity projects={projects.data ?? []} navigate={navigate} />
        ) : route === "settings" ? (
          <Settings health={health.data} />
        ) : route === "guide" ? (
          <OrderGuide onNew={() => setNewOrder(true)} navigate={navigate} />
        ) : route === "quotes" ? (
          <Projects
            projects={projects.data ?? []}
            navigate={navigate}
            onNew={() => setNewProject(true)}
          />
        ) : (
          <WorkQueue navigate={navigate} onNew={() => setNewProject(true)} />
        )}
      </div>
      {newOrder && (
        <NewOrder
          onClose={() => setNewOrder(false)}
          onCreated={(o) => {
            setNewOrder(false);
            qc.invalidateQueries({ queryKey: ["orders"] });
            qc.invalidateQueries({ queryKey: ["projects"] });
            navigate("order/" + o.id);
            notify(
              "Order created. Add the specifications and current approval package.",
            );
          }}
        />
      )}
      {newProject && (
        <NewProject
          onClose={() => setNewProject(false)}
          onCreated={(p) => {
            setNewProject(false);
            qc.invalidateQueries({ queryKey: ["projects"] });
            navigate("project/" + p.id);
            qc.invalidateQueries({ queryKey: ["work-queue"] });
            notify("Bid created. Add the existing quote and project change.");
          }}
        />
      )}
      {search && (
        <OrderSearch
          orders={orders.data?.orders ?? []}
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
  const [filter, setFilter] = useState("All quotes");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("updated");
  const needsReview = (p: Project) => p.blockers > 0 || p.pending > 0;
  const filters = [
    { label: "All quotes", count: projects.length },
    { label: "Needs review", count: projects.filter(needsReview).length },
    {
      label: "Approved",
      count: projects.filter((p) => p.status === "approved").length,
    },
  ];
  const filtered = projects
    .filter(
      (p) =>
        (filter === "All quotes" ||
          (filter === "Needs review" && needsReview(p)) ||
          (filter === "Approved" && p.status === "approved")) &&
        `${p.title} ${p.customer} ${p.number}`
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
    )
    .sort((a, b) =>
      sort === "name"
        ? a.title.localeCompare(b.title)
        : sort === "due"
          ? (a.due_date || "9999").localeCompare(b.due_date || "9999")
          : sort === "value"
            ? Number(b.total) - Number(a.total)
            : b.updated_at.localeCompare(a.updated_at),
    );
  const featured = projects.find((p) => p.pending > 0);
  const reset = () => {
    setFilter("All quotes");
    setQuery("");
  };
  return (
    <main className="page projects-page">
      <div className="page-heading">
        <div>
          <h1>Quotes</h1>
          <p>Browse every bid, its current quote, and its revision history.</p>
        </div>
        <button className="primary" onClick={onNew}>
          <Plus size={18} />
          New bid
        </button>
      </div>
      {featured && (
        <button
          className="review-notice"
          onClick={() => navigate("project/" + featured.id + "?changes")}
        >
          <GitDiff size={19} />
          <span>
            <strong>{featured.title}</strong> has {featured.pending} pending{" "}
            {featured.pending === 1 ? "change" : "changes"}.
          </span>
          <span className="review-notice-action">
            Review changes
            <ArrowRight size={16} />
          </span>
        </button>
      )}
      <section className="projects-table-section" aria-label="Projects">
        <div className="project-filters">
          <div className="segmented" aria-label="Filter projects">
            {filters.map((x) => (
              <button
                key={x.label}
                className={filter === x.label ? "selected" : ""}
                aria-pressed={filter === x.label}
                onClick={() => setFilter(x.label)}
              >
                {x.label}
                <span>{x.count}</span>
              </button>
            ))}
          </div>
          <div className="project-list-controls">
            <label className="compact-search">
              <MagnifyingGlass size={18} />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search projects…"
                aria-label="Find a project"
              />
            </label>
            <select
              aria-label="Sort projects"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="updated">Recently updated</option>
              <option value="due">Due date</option>
              <option value="name">Project name</option>
              <option value="value">Quote value</option>
            </select>
          </div>
        </div>
        <div className="project-table-head">
          <span>Project</span>
          <span>Status</span>
          <span>Quote value</span>
          <span>Due date</span>
          <span />
        </div>
        <div className="project-list">
          {filtered.map((p) => (
            <button
              className="project-row"
              key={p.id}
              aria-label={"Open " + p.title}
              onClick={() => navigate("project/" + p.id)}
            >
              <div className="project-name">
                <FolderSimple size={21} />
                <div>
                  <h3>{p.title}</h3>
                  <p>
                    {p.customer}
                    <span>·</span>
                    {p.number}
                  </p>
                </div>
              </div>
              <div className="project-status">
                <Status status={p.pending ? "pending" : p.status} />
                {p.blockers > 0 && (
                  <small>
                    {p.blockers} {p.blockers === 1 ? "check" : "checks"} to
                    resolve
                  </small>
                )}
              </div>
              <div className="project-value">{usd(p.total)}</div>
              <div className="due-date">{dateLabel(p.due_date)}</div>
              <CaretRight className="row-arrow" size={17} />
            </button>
          ))}
          {!filtered.length && (
            <Empty
              title={
                projects.length ? "No matching quotes" : "Create your first bid"
              }
              icon={
                projects.length ? (
                  <ListMagnifyingGlass size={30} />
                ) : (
                  <FolderSimple size={30} />
                )
              }
              action={
                <button
                  className="secondary"
                  onClick={projects.length ? reset : onNew}
                >
                  {projects.length ? "Clear filters" : "New bid"}
                </button>
              }
            >
              {projects.length
                ? "Try another project name, customer, or filter."
                : "Add a project, then upload the documents for your quote."}
            </Empty>
          )}
        </div>
        <div className="list-footer">
          <span>
            {filtered.length} of {projects.length} projects
          </span>
          <span>Amounts in USD</span>
        </div>
      </section>
      {projects.some((p) => p.synthetic) && (
        <div className="sample-disclosure">
          <Info size={16} />
          <span>
            Sample projects contain fictional customers, equipment, and prices.
          </span>
        </div>
      )}
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
    <Modal title="New bid" onClose={onClose}>
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
                category: "Low-voltage switchgear",
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
          Start with the existing quote and the project change. Rivet will keep
          the resulting decisions and open questions together.
        </p>
        <label>
          Project name
          <input
            name="title"
            required
            minLength={2}
            maxLength={180}
            placeholder="e.g. Northline Data Center · Phase 2"
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
            <input value="Low-voltage switchgear" readOnly />
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
                Create bid
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
    `${p.title} ${p.customer} ${p.number}`
      .toLowerCase()
      .includes(q.trim().toLowerCase()),
  );
  return (
    <Modal title="Search projects" onClose={onClose}>
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
          <Empty title="No matching quotes">
            Try another name or customer.
          </Empty>
        )}
      </div>
    </Modal>
  );
}
type CatalogEntry = {
  id: string;
  model: string;
  manufacturer: string;
  description: string;
  synthetic: boolean;
  offers: {
    id: string;
    supplier: string;
    currency: string;
    cost: string;
    unit: string;
    valid_until: string | null;
    lead_time: string;
    max_quantity?: string | null;
  }[];
};
function Catalog() {
  const [search, setSearch] = useState("");
  const [expandedOffers, setExpandedOffers] = useState<string | null>(null);
  const today = new Date();
  const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  type Offer = CatalogEntry["offers"][number];
  const hasExpiry = (offer: Offer) =>
    Boolean(offer.valid_until && /^\d{4}-\d{2}-\d{2}$/.test(offer.valid_until));
  const offerRank = (offer: Offer) =>
    !hasExpiry(offer) ? 1 : offer.valid_until! >= todayIso ? 0 : 2;
  const sortedOffers = (offers: Offer[]) =>
    [...offers].sort(
      (a, b) =>
        offerRank(a) - offerRank(b) ||
        (b.valid_until || "").localeCompare(a.valid_until || "") ||
        a.supplier.localeCompare(b.supplier) ||
        a.currency.localeCompare(b.currency) ||
        a.unit.localeCompare(b.unit) ||
        Number(a.cost) - Number(b.cost) ||
        a.id.localeCompare(b.id),
    );
  const expiryLabel = (offer: Offer) => {
    if (!hasExpiry(offer)) return "Expiry not provided";
    const date = new Date(offer.valid_until + "T12:00:00").toLocaleDateString(
      "en-US",
      { month: "short", day: "numeric", year: "numeric" },
    );
    return offerRank(offer) === 2 ? `Expired ${date}` : date;
  };
  const offerCost = (offer: Offer) =>
    offer.currency === "USD"
      ? usd(offer.cost, 2)
      : `${offer.currency} ${Number(offer.cost).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const { data, error, isLoading, refetch } = useQuery({
    queryKey: ["catalog"],
    queryFn: () => api<CatalogEntry[]>("/catalog"),
  });
  const matches =
    data?.filter((c) =>
      `${c.model} ${c.description} ${c.manufacturer} ${c.offers.map((offer) => offer.supplier).join(" ")}`
        .toLowerCase()
        .includes(search.trim().toLowerCase()),
    ) ?? [];
  return (
    <main className="page catalog-page">
      <div className="page-heading">
        <div>
          <h1>Equipment catalog</h1>
          <p>Product specifications and supplier offers for your quotes.</p>
        </div>
      </div>
      <div className="info-banner">
        <Info size={18} />
        <p>
          To add products or offers, open a project and import a spreadsheet in
          Sources.
        </p>
      </div>
      <div className="catalog-toolbar">
        <label className="compact-search">
          <MagnifyingGlass size={18} />
          <input
            placeholder="Search equipment, manufacturer, or supplier…"
            aria-label="Search equipment"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <span>{matches.length} products</span>
      </div>
      {isLoading && <Busy text="Loading equipment…" />}
      {error && (
        <>
          <ErrorNote message={(error as Error).message} />
          <button className="secondary" onClick={() => refetch()}>
            Try again
          </button>
        </>
      )}
      {!isLoading && !error && (
        <>
          {matches.length > 0 ? (
            <div
              className="catalog-table-scroll"
              role="region"
              aria-label="Equipment catalog table"
              tabIndex={0}
            >
              <table className="catalog-table">
                <thead>
                  <tr>
                    <th>Equipment</th>
                    <th>Manufacturer</th>
                    <th>Supplier cost</th>
                    <th>Lead time</th>
                    <th>Offer expiry</th>
                  </tr>
                </thead>
                <tbody>
                  {matches.flatMap((c) => {
                    const offers = sortedOffers(c.offers);
                    const offer = offers[0];
                    const expanded = expandedOffers === c.id;
                    const showQuantity = offers.some((item) =>
                      Object.prototype.hasOwnProperty.call(
                        item,
                        "max_quantity",
                      ),
                    );
                    return [
                      <tr key={c.id}>
                        <td>
                          <strong>{c.description}</strong>
                          <span className="catalog-model">
                            {c.model}
                            {c.synthetic && (
                              <span className="sample-label">Sample</span>
                            )}
                          </span>
                        </td>
                        <td>{c.manufacturer}</td>
                        <td className="catalog-cost">
                          {offer ? (
                            <>
                              <span>
                                {offerCost(offer)}
                                <small> / {offer.unit}</small>
                              </span>
                              <button
                                className="catalog-offers-toggle"
                                aria-expanded={expanded}
                                aria-controls={`catalog-offers-${c.id}`}
                                aria-label={`${expanded ? "Hide" : "View"} ${offers.length} supplier ${offers.length === 1 ? "offer" : "offers"} for ${c.model}`}
                                onClick={() =>
                                  setExpandedOffers(expanded ? null : c.id)
                                }
                              >
                                {offers.length}{" "}
                                {offers.length === 1 ? "offer" : "offers"}
                                <CaretRight size={12} />
                              </button>
                            </>
                          ) : (
                            "No offer"
                          )}
                        </td>
                        <td>{offer?.lead_time || "—"}</td>
                        <td
                          className={
                            offer && offerRank(offer) === 2
                              ? "catalog-expired"
                              : ""
                          }
                        >
                          {offer ? expiryLabel(offer) : "—"}
                        </td>
                      </tr>,
                      ...(expanded
                        ? [
                            <tr
                              key={`${c.id}-offers`}
                              className="catalog-offers-row"
                            >
                              <td colSpan={5}>
                                <div
                                  id={`catalog-offers-${c.id}`}
                                  className="catalog-offers-detail"
                                  role="region"
                                  aria-label={`Supplier offers for ${c.model}`}
                                >
                                  <div className="catalog-offers-heading">
                                    <h3>Supplier offers for {c.model}</h3>
                                    <p>
                                      Dated, unexpired offers appear first,
                                      ordered by latest expiry. Review the
                                      supplier and terms before selecting
                                      equipment.
                                    </p>
                                  </div>
                                  <table className="catalog-offers-table">
                                    <thead>
                                      <tr>
                                        <th scope="col">Supplier</th>
                                        <th scope="col">Unit cost</th>
                                        <th scope="col">Lead time</th>
                                        <th scope="col">Expiry</th>
                                        {showQuantity && (
                                          <th scope="col">Quantity coverage</th>
                                        )}
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {offers.map((item) => (
                                        <tr key={item.id}>
                                          <td>
                                            {item.supplier ||
                                              "Supplier not provided"}
                                          </td>
                                          <td className="catalog-cost">
                                            {offerCost(item)}
                                            <small> / {item.unit}</small>
                                          </td>
                                          <td>
                                            {item.lead_time || "Not provided"}
                                          </td>
                                          <td
                                            className={
                                              offerRank(item) === 2
                                                ? "catalog-expired"
                                                : ""
                                            }
                                          >
                                            {expiryLabel(item)}
                                          </td>
                                          {showQuantity && (
                                            <td>
                                              {item.max_quantity != null
                                                ? `Up to ${item.max_quantity} ${item.unit}`
                                                : "Not provided"}
                                            </td>
                                          )}
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                  {!showQuantity && (
                                    <p className="catalog-quantity-note">
                                      Check quantity coverage when selecting
                                      equipment in a quote.
                                    </p>
                                  )}
                                </div>
                              </td>
                            </tr>,
                          ]
                        : []),
                    ];
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title={
                data?.length ? "No matching equipment" : "No equipment yet"
              }
              icon={<Stack size={28} />}
              action={
                search ? (
                  <button className="secondary" onClick={() => setSearch("")}>
                    Clear search
                  </button>
                ) : undefined
              }
            >
              {data?.length
                ? "Try another model, manufacturer, supplier, or description."
                : "Import a catalog spreadsheet from a project’s Sources tab."}
            </Empty>
          )}
        </>
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
  const { data, isLoading, error, refetch } = useQuery({
    refetchInterval: 5000,
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
          <h1>Activity</h1>
          <p>Recent edits and review decisions across your projects.</p>
        </div>
      </div>
      {isLoading && <Busy text="Loading activity…" />}
      {error && (
        <>
          <ErrorNote message={(error as Error).message} />
          <button className="secondary" onClick={() => refetch()}>
            Try again
          </button>
        </>
      )}
      {data?.length === 0 && (
        <Empty title="No activity yet" icon={<Clock size={28} />}>
          Project edits and review decisions will appear here.
        </Empty>
      )}
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
  const account = useAccount();
  return (
    <main className="page narrow-page">
      <div className="page-heading">
        <div>
          <h1>Workspace settings</h1>
          <p>Accounts, email, and integration status.</p>
        </div>
      </div>
      <section className="settings-section">
        <h3>Workspace</h3>
        <div>
          <span>
            {account.team}
            <small>
              {account.mode === "clerk"
                ? "Access is restricted to signed-in team members."
                : "Local development workspace."}
            </small>
          </span>
          <Status status="ready" />
        </div>
        <div>
          <span>
            Document storage
            <small>
              PostgreSQL and private files on this device. Hosted storage has
              not been configured.
            </small>
          </span>
          <span className="micro-tag">Local</span>
        </div>
        <div>
          <span>
            AI assistant
            <small>
              {health?.assistant_configured ? health.model : "Not configured"}
            </small>
          </span>
          <Status
            status={health?.assistant_configured ? "ready" : "unreviewed"}
          />
        </div>
      </section>
      <IntegrationSettings />
      <TeamSettings />
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
          <h1>Getting started</h1>
          <p>
            Keep an existing quote current when a data center project changes.
          </p>
        </div>
      </div>
      <section
        className="sample-pack"
        aria-label="Switchgear bid coordination demo"
      >
        <div>
          <FileCsv size={22} />
          <div>
            <h3>Try the switchgear revision workflow</h3>
            <p>
              A fictional data center bid: eight sections become ten, with new
              pricing and a delivery question to resolve.
            </p>
          </div>
        </div>
        <div className="sample-pack-downloads">
          <a
            className="secondary"
            href={switchgearQuoteUrl}
            download="switchgear-current-quote.csv"
          >
            Current quote
            <DownloadSimple size={15} />
          </a>
          <a
            className="secondary"
            href={switchgearOfferUrl}
            download="switchgear-initial-offer.txt"
          >
            Initial offer
            <DownloadSimple size={15} />
          </a>
          <a
            className="secondary"
            href={switchgearAddendumUrl}
            download="switchgear-addendum-02.txt"
          >
            Addendum
            <DownloadSimple size={15} />
          </a>
          <a
            className="secondary"
            href={switchgearReplyUrl}
            download="switchgear-revised-offer.txt"
          >
            Revised offer
            <DownloadSimple size={15} />
          </a>
        </div>
        <p className="sample-pack-note">
          Start a new bid with the current quote and initial offer, then add the
          addendum. Record a clarification, upload the revised offer, and
          recheck. The proposed total moves from $140,000 to $187,500 at 20%
          gross margin. Delivery still needs a decision. All equipment and
          commercial details are fictional.
        </p>
      </section>
      <section className="sample-pack" aria-label="Workflow test files">
        <div>
          <FileCsv size={22} />
          <div>
            <h3>Try a complete quote</h3>
            <p>
              Two fictional equipment items, with a schedule and an addendum. No
              API key needed.
            </p>
          </div>
        </div>
        <div className="sample-pack-downloads">
          <a
            className="secondary"
            href={sampleScheduleUrl}
            download="equipment-schedule.csv"
          >
            <DownloadSimple size={17} />
            Equipment schedule
          </a>
          <a
            className="secondary"
            href={sampleAddendumUrl}
            download="addendum-02.csv"
          >
            <DownloadSimple size={17} />
            Addendum 02
          </a>
        </div>
        <p className="sample-pack-note">
          The schedule totals $92,000. Applying the addendum brings it to
          $112,000. Review both lines, then use Review &amp; export to approve
          and download your quote.
        </p>
      </section>
      <div className="guide-steps">
        {[
          [
            "01",
            "Bring the current quote",
            "Create a bid for your low-voltage switchgear package. Upload the existing quote and supplier offer. Confirm spreadsheet columns and review the imported baseline.",
          ],
          [
            "02",
            "Investigate the change",
            "Upload the new addendum. In Work, ask Rivet to compare it with the quote and supplier offer. Review scope, pricing, accessories, and delivery gaps against their sources.",
          ],
          [
            "03",
            "Move the open questions forward",
            "Review or write a clarification, copy the request to your email, and mark it as requested after sending it yourself. Set a recipient and due date so the waiting work stays visible.",
          ],
          [
            "04",
            "Resume when the answer arrives",
            "Upload the updated offer, record the reply, and link its source. Recheck with Rivet using the latest project inputs. Review proposed changes before accepting them, then resolve the issue with a note.",
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
        Create your bid
      </button>
    </main>
  );
}
