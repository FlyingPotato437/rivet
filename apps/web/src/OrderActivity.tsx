import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowUpRight } from "@phosphor-icons/react";
import { api, when } from "./api";
import { Busy, ErrorNote } from "./ui";
import { WorkDrawer } from "./WorkDrawer";
import { ConnectedExecutionDetails } from "./WorkDetails";
import { activityActor, type ActivityEvent } from "./work-presentation";
import type { LifecycleView } from "./lifecycle-work";
import "./order-activity.css";

export function OrderActivity({
  orderId,
  navigate,
}: {
  orderId?: string;
  navigate: (to: string) => void;
}) {
  const [order, setOrder] = useState(
    orderId ||
      new URLSearchParams(location.hash.split("?")[1] || "").get("order") ||
      "all",
  );
  const [actor, setActor] = useState("all"),
    [selected, setSelected] = useState<ActivityEvent | null>(null);
  const q = useQuery({
    queryKey: ["order-activity", orderId],
    queryFn: () =>
      api<{ items: ActivityEvent[] }>(
        orderId ? `/orders/${orderId}/timeline` : "/order-activity",
      ),
    refetchInterval: 10000,
  });
  const options = [
    ...new Map(
      (q.data?.items || []).map((e) => [e.order_id, e.number]),
    ).entries(),
  ];
  const visible = (q.data?.items || []).filter(
    (e) =>
      (order === "all" || e.order_id === order) &&
      (actor === "all" || activityActor(e) === actor),
  );
  const Heading = orderId ? "h2" : "h1";
  const content = (
    <>
      <header className="order-activity-heading">
        <div>
          <span className="story-overline">Workspace history</span>
          <Heading>{orderId ? "Order activity" : "Activity"}</Heading>
          <p>The full record of work, decisions and connected systems.</p>
        </div>
      </header>
      <div className="activity-filters">
        {!orderId && (
          <label>
            Order
            <select
              aria-label="Filter activity by order"
              value={order}
              onChange={(e) => setOrder(e.target.value)}
            >
              <option value="all">All orders</option>
              {options.map(([id, number]) => (
                <option key={id} value={id}>
                  {number}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          Who
          <select
            aria-label="Filter activity by actor"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          >
            <option value="all">Everyone</option>
            <option value="rivet">Rivet</option>
            <option value="people">People</option>
            <option value="systems">Systems</option>
          </select>
        </label>
      </div>
      {q.isError && (
        <ErrorNote message="Activity could not load. Try again shortly." />
      )}
      {q.isPending ? (
        <Busy text="Loading activity…" />
      ) : !visible.length ? (
        <p className="activity-empty">No activity matches these filters.</p>
      ) : (
        <ol className="audit-event-list">
          {visible.map((e) => (
            <li key={e.kind + e.id}>
              <button onClick={() => setSelected(e)}>
                <span className={`audit-actor is-${activityActor(e)}`}>
                  {activityActor(e) === "rivet"
                    ? "Rivet"
                    : activityActor(e) === "people"
                      ? "People"
                      : "System"}
                </span>
                <span>
                  <strong>{e.title}</strong>
                  <small>{e.number}</small>
                </span>
                <time dateTime={e.at}>{when(e.at)}</time>
              </button>
            </li>
          ))}
        </ol>
      )}
      {selected && (
        <WorkDrawer
          title={selected.title}
          eyebrow={`${selected.number} · ${when(selected.at)}`}
          close={() => setSelected(null)}
        >
          <ActivityDetails event={selected} />
          <button
            className="text-button"
            onClick={() => navigate(`order/${selected.order_id}`)}
          >
            Open order <ArrowUpRight size={14} />
          </button>
        </WorkDrawer>
      )}
    </>
  );
  return orderId ? (
    <section className="order-activity">{content}</section>
  ) : (
    <main className="page order-activity activity-page">{content}</main>
  );
}
function ActivityDetails({ event }: { event: ActivityEvent }) {
  const q = useQuery({
    queryKey: ["order-lifecycle", event.order_id],
    queryFn: () => api<LifecycleView>(`/orders/${event.order_id}/lifecycle`),
  });
  return (
    <>
      <ConnectedExecutionDetails orderId={event.order_id} />
      <div className="drawer-reading">
        <Markdown remarkPlugins={[remarkGfm]} disallowedElements={["img"]}>
          {event.detail}
        </Markdown>
      </div>
      {q.isPending ? (
        <Busy text="Loading connected work…" />
      ) : q.isError ? (
        <ErrorNote message="Connected-system details could not load." />
      ) : (
        q.data?.packages.map((p) => (
          <section className="audit-package" key={p.id}>
            <h3>{p.title}</h3>
            {/local|demo|test|simulat/i.test(p.mode) && (
              <span className="audit-test-badge">Test environment</span>
            )}
            <dl className="drawer-facts">
              <dt>Status</dt>
              <dd>{p.status}</dd>
              <dt>Source version</dt>
              <dd className="drawer-code">{p.fingerprint}</dd>
              <dt>Prepared</dt>
              <dd>{when(p.prepared_at)}</dd>
            </dl>
            {p.receipts.length > 0 && (
              <>
                <h4>Execution receipts</h4>
                <ul>
                  {p.receipts.map((r, i) => (
                    <li key={i}>
                      <strong>
                        {r.system} · {r.action}
                      </strong>
                      <p>
                        {r.status} · {r.mode}
                      </p>
                      <p>{r.notice}</p>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {p.decisions.length > 0 && (
              <>
                <h4>Approvals</h4>
                <ul>
                  {p.decisions.map((d, i) => (
                    <li key={i}>
                      {d.gate} · {d.decision} · {when(d.at)}
                      <p>
                        {d.actor}: {d.reason}
                      </p>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
        ))
      )}
      {event.outlook_url && (
        <a href={event.outlook_url} target="_blank" rel="noreferrer">
          Open in Outlook
        </a>
      )}
    </>
  );
}
