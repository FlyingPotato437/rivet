import { useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Check,
  EnvelopeSimple,
  Plus,
  WarningCircle,
} from "@phosphor-icons/react";
import { api, when } from "./api";
import { useProjectWork } from "./ProjectHub";
import { CheckInboxNow } from "./YourWork";
import { IntakeConfirmation, type Intake } from "./RecordIntake";
import { useRecordOrders } from "./RecordFeed";
import { WorkDrawer } from "./WorkDrawer";
import {
  DecisionCard,
  DecisionDetails,
  SourceDetails,
  StoryDetails,
  ReplyAction,
} from "./WorkDetails";
import { ErrorNote, RivetIcon } from "./ui";
import type { LifecycleView } from "./lifecycle-work";
import type { ProjectFlow } from "./project-flow";
import type { WorkMailbox } from "./mailbox-check";
import {
  atRisk,
  decisionItems,
  workingSentence,
  rivetToday,
  todayReplies,
  pmSentence,
  type DailyBrief,
  type ActivityEvent,
  type DecisionItem,
} from "./work-presentation";
import "./today-story.css";
import {
  orderOutcome,
  replyPreview,
  todayWorkSummary,
} from "./workspace-outcomes";
import "./workspace-refinement.css";
import { EquipmentCorrections } from "./EquipmentCorrections";

type Drawer =
  | { kind: "decision"; item: DecisionItem }
  | { kind: "reply"; order: ProjectFlow }
  | { kind: "source"; orderId: string; id: string }
  | { kind: "question"; item: Intake }
  | { kind: "issue"; order: ProjectFlow };
export function Today({
  navigate,
  onNew,
}: {
  navigate: (to: string) => void;
  onNew: () => void;
}) {
  const orders = useProjectWork();
  const qc = useQueryClient(),
    recordOrders = useRecordOrders();
  const incoming = useQuery({
    queryKey: ["record-intake", "pending"],
    queryFn: () =>
      api<{ items: Intake[] }>("/records/intake?status=needs_confirmation"),
    refetchInterval: 15000,
  });
  const brief = useQuery({
    queryKey: ["daily-brief"],
    queryFn: () => api<DailyBrief>("/daily-brief"),
    refetchInterval: 8000,
  });
  const activity = useQuery({
    queryKey: ["order-activity", undefined],
    queryFn: () => api<{ items: ActivityEvent[] }>("/order-activity"),
    refetchInterval: 8000,
  });
  const mailboxes = useQuery({
    queryKey: ["work-mailboxes"],
    queryFn: () => api<{ items: WorkMailbox[] }>("/mailboxes"),
    refetchInterval: 30000,
  });
  const decisionOrders = [
    ...new Set((brief.data?.decisions || []).map((d) => d.order_id)),
  ];
  const packages = useQueries({
    queries: decisionOrders.map((id) => ({
      queryKey: ["order-lifecycle", id],
      queryFn: () => api<LifecycleView>(`/orders/${id}/lifecycle`),
      refetchInterval: 8000,
    })),
  });
  const [drawer, setDrawer] = useState<Drawer | null>(null),
    [showMore, setShowMore] = useState(false),
    [approvedOrder, setApprovedOrder] = useState<ProjectFlow | null>(null);
  const all = orders.data?.projects || [];
  const decisions = packages
    .flatMap((q, i) =>
      decisionItems(q.data?.packages || [], {
        id: decisionOrders[i],
        version: q.data?.version,
        number:
          all.find((o) => o.id === decisionOrders[i])?.number ||
          brief.data?.decisions.find((d) => d.order_id === decisionOrders[i])
            ?.number ||
          "Order",
        customer:
          all.find((o) => o.id === decisionOrders[i])?.customer || undefined,
      }),
    )
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const replies = todayReplies(all),
    risks = atRisk(all, brief.data?.waiting_items);
  const feed = rivetToday(activity.data?.items || brief.data?.items || []);
  const connected = mailboxes.data?.items.filter((m) => m.enabled) || [];
  const working = all.filter((p) => p.status === "handling");
  const live = workingSentence(brief.data?.working || [], working.length);
  const issues = all.filter(
    (p) =>
      p.status === "needs_you" && !decisions.some((d) => d.orderId === p.id),
  );
  const questions = incoming.data?.items || [];
  const missingDecisions =
    brief.data?.decisions.length &&
    !decisions.length &&
    packages.some((q) => q.isError);
  const loading =
    orders.isPending ||
    brief.isPending ||
    incoming.isPending ||
    packages.some((q) => q.isPending);
  const failed =
    orders.isError ||
    brief.isError ||
    activity.isError ||
    incoming.isError ||
    !!missingDecisions;
  const needs = decisions.length + questions.length + issues.length;
  const open = (item: Drawer) => setDrawer(item);
  return (
    <main className="page today-page">
      <header className="today-heading">
        <div>
          <span className="story-overline">
            Today ·{" "}
            {new Intl.DateTimeFormat("en-US", {
              weekday: "long",
              month: "short",
              day: "numeric",
            }).format(new Date())}
          </span>
          <h1>Today</h1>
          <p>
            {loading
              ? "Getting your day ready…"
              : failed
                ? "Some updates are delayed. These are the last saved decisions."
                : todayWorkSummary({
                    ready: replies.filter(
                      (p) => p.status === "ready" && p.outlook.native,
                    ).length,
                    prepared: replies.filter(
                      (p) => p.status !== "ready" || !p.outlook.native,
                    ).length,
                    decisions: needs,
                    working: working.length,
                  })}
          </p>
        </div>
        <button className="secondary" onClick={onNew}>
          <Plus size={16} />
          New order
        </button>
      </header>
      <div className="today-connection">
        <span
          className={`story-watch-dot${connected.length ? " is-connected" : ""}`}
        />
        <span>
          {connected.length
            ? `Outlook connected · ${connected[0].mailbox || "Your mailbox"}${connected.length > 1 ? ` and ${connected.length - 1} more` : ""}`
            : "Connect Outlook to capture order mail automatically"}
        </span>
        {connected.length ? (
          <CheckInboxNow mailboxes={connected} />
        ) : (
          <button className="text-button" onClick={() => navigate("settings")}>
            Connect Outlook
          </button>
        )}
      </div>
      {failed && (
        <ErrorNote message="Some updates could not load. Your last saved work is shown; refresh before making a decision." />
      )}
      {approvedOrder && (
        <div className="today-approved" role="status">
          <Check size={18} />
          <span>Your approval was saved for {approvedOrder.number}.</span>
          {approvedOrder.outlook.native && (
            <ReplyAction order={approvedOrder} />
          )}
          <button
            className="text-button"
            onClick={() => setApprovedOrder(null)}
          >
            Dismiss
          </button>
        </div>
      )}
      {!!replies.length && (
        <section
          className="today-section today-prepared"
          aria-label="Prepared for you"
        >
          <header className="today-section-heading">
            <div>
              <span className="section-eyebrow">Rivet’s finished work</span>
              <h2>
                Prepared for you <span>{replies.length}</span>
              </h2>
            </div>
            <p>Open the draft. Everything else is already together.</p>
          </header>
          <div className="today-prepared-list">
            {replies.map((p) => (
              <PreparedReplyRow
                key={p.id}
                order={p}
                navigate={navigate}
                open={() => open({ kind: "reply", order: p })}
              />
            ))}
          </div>
        </section>
      )}
      {(needs > 0 || loading || failed || !replies.length) && (
        <section
          className="today-section today-decisions-section"
          aria-label="Needs you"
        >
          <header className="today-section-heading">
            <h2>
              Decisions <span>{needs || ""}</span>
            </h2>
            <p>Only the points Rivet cannot decide for you.</p>
          </header>
          {loading ? (
            <div className="today-skeleton" aria-label="Loading decisions">
              <div />
              <div />
            </div>
          ) : (
            <>
              {questions.map((item) => (
                <article className="today-decision" key={item.intake_id}>
                  <span className="decision-symbol">
                    <EnvelopeSimple size={18} />
                  </span>
                  <div className="today-decision-copy">
                    <span className="story-overline">One quick question</span>
                    <h3>
                      {item.email?.subject ||
                        "Which order does this message belong to?"}
                    </h3>
                    <p>
                      {pmSentence(
                        item.reason,
                        "Rivet found a possible order match. Check the message before filing it.",
                      )}
                    </p>
                  </div>
                  <div className="today-decision-actions">
                    <button
                      className="primary"
                      onClick={() => open({ kind: "question", item })}
                    >
                      Check order match
                    </button>
                  </div>
                </article>
              ))}
              {issues.map((p) => (
                <article className="today-decision" key={p.id}>
                  <span className="decision-symbol">
                    <WarningCircle size={18} />
                  </span>
                  <div className="today-decision-copy">
                    <span className="story-overline">{p.number}</span>
                    <h3>Rivet needs a hand with {p.name}</h3>
                    <p>
                      {pmSentence(
                        p.explanation,
                        "Rivet paused this order. Open the details to see what’s needed.",
                      )}
                    </p>
                  </div>
                  <div className="today-decision-actions">
                    <button
                      className="primary"
                      onClick={() => open({ kind: "issue", order: p })}
                    >
                      See what’s needed
                    </button>
                  </div>
                </article>
              ))}
              {decisions.map((item) => (
                <DecisionCard
                  key={item.id}
                  item={item}
                  seeWhy={() => open({ kind: "decision", item })}
                  onDecided={() =>
                    setApprovedOrder(
                      all.find((p) => p.id === item.orderId) || null,
                    )
                  }
                />
              ))}
              {!needs && !replies.length && !failed && (
                <div className="today-caught-up">
                  <span>
                    <Check size={21} />
                  </span>
                  <div>
                    <h3>You’re all caught up.</h3>
                    <p>
                      {connected.length
                        ? `Rivet is watching ${connected[0].mailbox || "your mailbox"}.`
                        : "Connect Outlook once to let Rivet watch for new order mail."}
                    </p>
                  </div>
                </div>
              )}
            </>
          )}
        </section>
      )}
      {live && (
        <div className="today-working" role="status">
          <RivetIcon name="agent" size={20} />
          <span>{live.text}</span>
          {!!live.total && (
            <span
              className="today-working-progress"
              aria-label={`${live.done} of ${live.total} comments prepared`}
            >
              <span
                style={{
                  width: `${Math.round((live.done / live.total) * 100)}%`,
                }}
              />
            </span>
          )}
          <button
            className="text-button"
            onClick={() => navigate(`order/${live.orderId || working[0].id}`)}
          >
            See progress
          </button>
        </div>
      )}
      <div className="today-lower-grid">
        <section className="today-section today-feed" aria-label="Rivet today">
          <header className="today-section-heading">
            <h2>Completed work</h2>
            <button
              className="text-button"
              onClick={() => navigate("activity")}
            >
              All activity
            </button>
          </header>
          {activity.isPending ? (
            <div className="today-skeleton">
              <div />
            </div>
          ) : feed.length ? (
            <>
              <ol>
                {feed
                  .slice(0, showMore ? undefined : 8)
                  .map(({ event, title, count }) => (
                    <li key={event.kind + event.id}>
                      <button
                        onClick={() =>
                          navigate(
                            `order/${event.order_id}?event=${encodeURIComponent(`event:${event.kind}:${event.id}`)}`,
                          )
                        }
                      >
                        <Check className="today-feed-check" size={16} />
                        <span>
                          <strong>{title}</strong>
                          <small>
                            {event.number}
                            {count > 1 ? ` · ${count} updates` : ""}
                          </small>
                        </span>
                        <time dateTime={event.at}>
                          {new Intl.DateTimeFormat("en-US", {
                            hour: "numeric",
                            minute: "2-digit",
                          }).format(new Date(event.at))}
                        </time>
                      </button>
                    </li>
                  ))}
              </ol>
              {feed.length > 8 && (
                <button
                  className="text-button"
                  onClick={() => setShowMore(!showMore)}
                >
                  {showMore ? "Show recent work" : "Show all today’s work"}
                </button>
              )}
            </>
          ) : (
            <p className="story-empty">
              Completed work will appear here as Rivet handles incoming orders.
            </p>
          )}
        </section>
        <section className="today-section today-risks" aria-label="At risk">
          <header className="today-section-heading">
            <h2>
              At risk <span>{risks.length || ""}</span>
            </h2>
          </header>
          {risks.length ? (
            risks.map((r) => (
              <article key={r.id}>
                <WarningCircle size={18} />
                <div>
                  <h3>
                    {r.order.customer || r.order.name} hasn’t replied in{" "}
                    {r.days} days.
                  </h3>
                  <p>
                    {r.drafted
                      ? "A follow-up is drafted in Outlook."
                      : r.status === "needs_check"
                        ? "Check the follow-up in Outlook before retrying."
                        : "The follow-up timer is due."}
                  </p>
                  <button
                    className="text-button"
                    onClick={() => navigate(`order/${r.order_id}`)}
                  >
                    Open order <ArrowUpRight size={13} />
                  </button>
                </div>
              </article>
            ))
          ) : (
            <p className="story-empty">
              {brief.isPending
                ? "Checking waiting orders…"
                : brief.isError
                  ? "Waiting-order updates are unavailable."
                  : "No overdue customer responses."}
            </p>
          )}
        </section>
      </div>
      {drawer && (
        <WorkDrawer
          title={
            drawer.kind === "decision"
              ? drawer.item.title
              : drawer.kind === "source"
                ? "Source"
                : drawer.kind === "question"
                  ? "Confirm the order"
                  : drawer.kind === "issue"
                    ? "What’s needed"
                    : drawer.order.equipment_correction
                      ? "Corrected drawing and BOM"
                      : "Rivet’s prepared response"
          }
          eyebrow={
            drawer.kind === "decision"
              ? drawer.item.number
              : drawer.kind === "reply" || drawer.kind === "issue"
                ? drawer.order.number
                : "Supporting evidence"
          }
          close={() => setDrawer(null)}
        >
          {drawer.kind === "decision" ? (
            <DecisionDetails
              item={drawer.item}
              navigate={navigate}
              openSource={(id) =>
                setDrawer({ kind: "source", orderId: drawer.item.orderId, id })
              }
            />
          ) : drawer.kind === "source" ? (
            <SourceDetails
              orderId={drawer.orderId}
              sourceId={drawer.id}
              close={() => setDrawer(null)}
            />
          ) : drawer.kind === "question" ? (
            <IntakeConfirmation
              item={drawer.item}
              orders={recordOrders.data?.orders || []}
              initiallyExpanded
              confirmed={() => {
                setDrawer(null);
                void Promise.all(
                  ["record-intake", "project-work", "record-inbox"].map((key) =>
                    qc.invalidateQueries({ queryKey: [key] }),
                  ),
                );
              }}
            />
          ) : drawer.kind === "issue" ? (
            <>
              <p className="drawer-intro">{drawer.order.explanation}</p>
              {!!drawer.order.open_points.length && (
                <ul>
                  {drawer.order.open_points.map((point, i) => (
                    <li key={i}>{point}</li>
                  ))}
                </ul>
              )}
              <button
                className="primary"
                onClick={() => navigate(`order/${drawer.order.id}`)}
              >
                Open order
              </button>
            </>
          ) : (
            <TodayReply
              order={drawer.order}
              openSource={(id) =>
                setDrawer({ kind: "source", orderId: drawer.order.id, id })
              }
            />
          )}
        </WorkDrawer>
      )}
    </main>
  );
}

function PreparedReplyRow({
  order,
  navigate,
  open,
}: {
  order: ProjectFlow;
  navigate: (to: string) => void;
  open: () => void;
}) {
  const outcome = orderOutcome(order),
    preview = replyPreview(order);
  return (
    <article className={`prepared-work-row is-${outcome.tone}`}>
      <div className="prepared-work-symbol">
        <RivetIcon name="handoff" size={22} />
      </div>
      <div className="prepared-work-copy">
        <div className="prepared-work-meta">
          <button onClick={() => navigate(`order/${order.id}`)}>
            {order.number}
          </button>
          <span>{order.customer || order.name}</span>
        </div>
        <button className="prepared-work-title" onClick={open}>
          {order.reply_preparation?.email_subject ||
            order.last_communication?.subject ||
            order.name}
        </button>
        <p>
          {preview ||
            pmSentence(
              order.explanation,
              "Rivet prepared the response with the available order sources.",
            )}
        </p>
        <div className="prepared-work-foot">
          <span className={`work-state is-${outcome.tone}`}>
            <span />
            {outcome.label}
          </span>
          {order.open_points.length > 0 && (
            <span>
              {order.open_points.length}{" "}
              {order.open_points.length === 1 ? "question" : "questions"}{" "}
              included in the draft
            </span>
          )}
        </div>
      </div>
      <div className="prepared-work-actions">
        {order.outlook.native ? (
          <>
            <ReplyAction order={order} />
            <button className="text-button" onClick={open}>
              {order.equipment_correction ? "Files and details" : "Read here"}
            </button>
          </>
        ) : (
          <button className="secondary" onClick={open}>
            Read response
          </button>
        )}
      </div>
    </article>
  );
}

function TodayReply({
  order,
  openSource,
}: {
  order: ProjectFlow;
  openSource: (id: string) => void;
}) {
  const q = useQuery({
    queryKey: ["project-work", order.id],
    queryFn: () => api<ProjectFlow>(`/project-work/${order.id}`),
    refetchInterval: 8000,
  });
  if ((q.data || order).equipment_correction)
    return <EquipmentCorrections id={order.id} />;
  return q.isPending ? (
    <p>Loading response…</p>
  ) : q.isError ? (
    <ErrorNote message="The response could not load. Try again." />
  ) : (
    <StoryDetails
      order={q.data || order}
      event={{
        id: "reply",
        orderId: order.id,
        at: order.updated_at,
        title: "Prepared response",
        state: "current",
        reply: true,
      }}
      openSource={openSource}
    />
  );
}
