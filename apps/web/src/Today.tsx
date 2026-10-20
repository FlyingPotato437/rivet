import { useState, type ReactNode } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus } from "@phosphor-icons/react";
import { api, when } from "./api";
import { useProjectWork } from "./ProjectHub";
import { CheckInboxNow } from "./YourWork";
import { IntakeConfirmation, type Intake } from "./RecordIntake";
import { useRecordOrders } from "./RecordFeed";
import { WorkDrawer } from "./WorkDrawer";
import {
  DecisionDetails,
  SourceDetails,
  StoryDetails,
  ReplyAction,
  decisionLabel,
  useDecision,
} from "./WorkDetails";
import { ErrorNote } from "./ui";
import { useAccount } from "./Auth";
import {
  BlueprintThumb,
  CustomerChip,
  RivetAvatar,
  StatusIcon,
  type StatusKind,
} from "./StatusIcon";
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
import { replyPreview } from "./workspace-outcomes";
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
  const account = useAccount();
  const [view, setView] = useState<TodayView>("all");
  const first = account.name.includes("@") ? "" : account.name.split(" ")[0];
  const hour = new Date().getHours();
  const greeting = `${hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"}${first ? `, ${first}` : ""}.`;
  const customerOf = (id: string) =>
    all.find((o) => o.id === id)?.customer || undefined;
  const counts: Record<TodayView, number> = {
    all: needs + working.length + replies.length + risks.length,
    needs,
    working: working.length,
    waiting: risks.length,
  };
  const shows = (group: TodayView) => view === "all" || view === group;
  const empty = !loading && !counts.all;
  return (
    <main className="page today-page today-board">
      <header className="today-hero">
        <div>
          <span className="today-date">
            {new Intl.DateTimeFormat("en-US", {
              weekday: "long",
              month: "short",
              day: "numeric",
            }).format(new Date())}
          </span>
          <h1>
            {greeting}
            <span>
              {loading
                ? "Getting your day ready…"
                : failed
                  ? "Some updates are delayed."
                  : needs
                    ? `${needs} ${needs === 1 ? "decision needs" : "decisions need"} you.`
                    : replies.length
                      ? `${replies.length} ${replies.length === 1 ? "reply is" : "replies are"} ready in Outlook.`
                      : "You’re all caught up."}
            </span>
          </h1>
        </div>
        <div className="today-hero-actions">
          <span
            className={`today-outlook${connected.length ? " is-connected" : ""}`}
            title={connected[0]?.mailbox || undefined}
          >
            <span />
            {connected.length ? "Outlook connected" : "Outlook not connected"}
          </span>
          {connected.length ? (
            <CheckInboxNow mailboxes={connected} />
          ) : (
            <button
              className="text-button"
              onClick={() => navigate("settings")}
            >
              Connect Outlook
            </button>
          )}
          <button className="secondary" onClick={onNew}>
            <Plus size={15} />
            New order
          </button>
        </div>
      </header>
      <div className="today-toolbar" role="tablist" aria-label="Show">
        {(
          [
            ["all", "All"],
            ["needs", "Needs you"],
            ["working", "In progress"],
            ["waiting", "Waiting"],
          ] as [TodayView, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={view === key}
            className={view === key ? "is-active" : ""}
            onClick={() => setView(key)}
          >
            {label}
            <span>{loading ? "" : counts[key]}</span>
          </button>
        ))}
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
      <div className="today-layout">
        <section className="work-list" aria-label="Today’s work">
          <div className="work-list-head" aria-hidden>
            <span />
            <span>Order</span>
            <span />
            <span>Decision</span>
            <span>Customer</span>
            <span>Updated</span>
            <span />
          </div>
          {loading ? (
            <div className="work-list-skeleton" aria-label="Loading decisions">
              <div />
              <div />
              <div />
            </div>
          ) : empty ? (
            <div className="today-caught-up">
              <StatusIcon kind="done" size={18} />
              <div>
                <h3>You’re all caught up.</h3>
                <p>
                  {connected.length
                    ? `Rivet is watching ${connected[0].mailbox || "your mailbox"}.`
                    : "Connect Outlook once to let Rivet watch for new order mail."}
                </p>
              </div>
            </div>
          ) : (
            <>
              {shows("needs") && needs > 0 && (
                <WorkGroup kind="need" label="Needs you" count={needs}>
                  {questions.map((item) => (
                    <WorkRow
                      key={item.intake_id}
                      kind="need"
                      number={item.detected.job_number || "Inbox"}
                      title={
                        item.email?.subject ||
                        "Which order does this message belong to?"
                      }
                      customer={item.email?.from}
                      at={item.email?.date}
                      cloud
                      onOpen={() => open({ kind: "question", item })}
                    >
                      <button
                        className="secondary"
                        onClick={() => open({ kind: "question", item })}
                      >
                        Check match
                      </button>
                    </WorkRow>
                  ))}
                  {issues.map((p) => (
                    <WorkRow
                      key={p.id}
                      kind="need"
                      number={p.number}
                      onNumber={() => navigate(`order/${p.id}`)}
                      title={`Rivet needs a hand · ${pmSentence(
                        p.explanation,
                        "Open the details to see what’s needed.",
                      )}`}
                      customer={p.customer || p.name}
                      at={p.updated_at}
                      cloud
                      onOpen={() => open({ kind: "issue", order: p })}
                    >
                      <button
                        className="secondary"
                        onClick={() => open({ kind: "issue", order: p })}
                      >
                        See what’s needed
                      </button>
                    </WorkRow>
                  ))}
                  {decisions.map((item) => (
                    <DecisionRow
                      key={item.id}
                      item={item}
                      customer={customerOf(item.orderId)}
                      navigate={navigate}
                      seeWhy={() => open({ kind: "decision", item })}
                      onDecided={() =>
                        setApprovedOrder(
                          all.find((p) => p.id === item.orderId) || null,
                        )
                      }
                    />
                  ))}
                </WorkGroup>
              )}
              {shows("working") && working.length > 0 && (
                <WorkGroup
                  kind="working"
                  label="Rivet is working"
                  count={working.length}
                >
                  {working.map((p) => {
                    const progress =
                      live && live.orderId === p.id && live.total ? live : null;
                    return (
                      <WorkRow
                        key={p.id}
                        kind="working"
                        number={p.number}
                        onNumber={() => navigate(`order/${p.id}`)}
                        title={
                          progress
                            ? `Preparing comment responses · ${progress.done} of ${progress.total}`
                            : pmSentence(
                                p.explanation,
                                "Rivet is preparing this order.",
                              )
                        }
                        customer={p.customer || p.name}
                        at={p.updated_at}
                        onOpen={() => navigate(`order/${p.id}`)}
                      >
                        {progress ? (
                          <span
                            className="work-progress"
                            aria-label={`${progress.done} of ${progress.total} comments prepared`}
                          >
                            <span>
                              <span
                                style={{
                                  width: `${Math.round((progress.done / progress.total) * 100)}%`,
                                }}
                              />
                            </span>
                            {progress.done}/{progress.total}
                          </span>
                        ) : (
                          <button
                            className="text-button"
                            onClick={() => navigate(`order/${p.id}`)}
                          >
                            See progress
                          </button>
                        )}
                      </WorkRow>
                    );
                  })}
                </WorkGroup>
              )}
              {view === "all" && replies.length > 0 && (
                <WorkGroup
                  kind="done"
                  label="Ready in Outlook"
                  count={replies.length}
                >
                  {replies.map((p) => (
                    <WorkRow
                      key={p.id}
                      kind="done"
                      number={p.number}
                      onNumber={() => navigate(`order/${p.id}`)}
                      title={
                        replyPreview(p) ||
                        p.reply_preparation?.email_subject ||
                        p.last_communication?.subject ||
                        p.name
                      }
                      customer={p.customer || p.name}
                      at={p.updated_at}
                      onOpen={() => open({ kind: "reply", order: p })}
                    >
                      {p.outlook.native ? (
                        <span className="rivet-output">
                          <ReplyAction order={p} label="Open draft" />
                        </span>
                      ) : (
                        <button
                          className="secondary"
                          onClick={() => open({ kind: "reply", order: p })}
                        >
                          Read reply
                        </button>
                      )}
                    </WorkRow>
                  ))}
                </WorkGroup>
              )}
              {shows("waiting") && risks.length > 0 && (
                <WorkGroup
                  kind="waiting"
                  label="Waiting on customer"
                  count={risks.length}
                >
                  {risks.map((r) => (
                    <WorkRow
                      key={r.id}
                      kind="waiting"
                      number={r.order.number}
                      onNumber={() => navigate(`order/${r.order_id}`)}
                      title={`No reply in ${r.days} ${r.days === 1 ? "day" : "days"} · ${
                        r.drafted
                          ? "follow-up drafted in Outlook"
                          : r.status === "needs_check"
                            ? "check the follow-up in Outlook"
                            : "follow-up is due"
                      }`}
                      customer={r.order.customer || r.order.name}
                      at={r.sent_at}
                      onOpen={() => navigate(`order/${r.order_id}`)}
                    >
                      <button
                        className="secondary"
                        onClick={() => navigate(`order/${r.order_id}`)}
                      >
                        Open order
                      </button>
                    </WorkRow>
                  ))}
                </WorkGroup>
              )}
              {view !== "all" && !counts[view] && (
                <p className="story-empty work-list-empty">
                  Nothing here right now.
                </p>
              )}
            </>
          )}
        </section>
        <aside className="today-activity" aria-label="Rivet today">
          <header>
            <h2>Activity</h2>
            <button
              className="text-button"
              onClick={() => navigate("activity")}
            >
              View all
            </button>
          </header>
          {activity.isPending ? (
            <div className="work-list-skeleton">
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
                        <RivetAvatar />
                        <span>
                          <span className="activity-copy">
                            {title.startsWith("Rivet ") ? (
                              <>
                                <b>Rivet</b>
                                {title.slice(5)}
                              </>
                            ) : (
                              title
                            )}
                          </span>
                          <small>
                            <span>{event.number}</span>
                            {count > 1 ? ` · ${count} updates` : ""}
                            {" · "}
                            <time dateTime={event.at}>
                              {new Intl.DateTimeFormat("en-US", {
                                hour: "numeric",
                                minute: "2-digit",
                              }).format(new Date(event.at))}
                            </time>
                          </small>
                        </span>
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
        </aside>
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

type TodayView = "all" | "needs" | "working" | "waiting";

function shortTime(at?: string) {
  if (!at || Number.isNaN(Date.parse(at))) return "";
  const date = new Date(at),
    now = new Date();
  if (date.toDateString() === now.toDateString())
    return new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
    }).format(date);
  if (now.getTime() - date.getTime() < 6 * 86400000)
    return new Intl.DateTimeFormat("en-US", { weekday: "short" }).format(date);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
  }).format(date);
}

function WorkGroup({
  kind,
  label,
  count,
  children,
}: {
  kind: StatusKind;
  label: string;
  count: number;
  children: ReactNode;
}) {
  return (
    <div className="work-group" role="group" aria-label={label}>
      <div className="work-group-head">
        <StatusIcon kind={kind} />
        <span>{label}</span>
        <span className="work-group-count">{count}</span>
      </div>
      {children}
    </div>
  );
}

function WorkRow({
  kind,
  number,
  onNumber,
  title,
  customer,
  at,
  cloud = false,
  onOpen,
  children,
}: {
  kind: StatusKind;
  number: string;
  onNumber?: () => void;
  title: string;
  customer?: string;
  at?: string;
  cloud?: boolean;
  onOpen: () => void;
  children?: ReactNode;
}) {
  return (
    <div className={`work-row is-${kind}`}>
      <StatusIcon kind={kind} />
      {onNumber ? (
        <button className="work-row-id" onClick={onNumber}>
          {number}
        </button>
      ) : (
        <span className="work-row-id">{number}</span>
      )}
      <BlueprintThumb cloud={cloud} />
      <button className="work-row-title" onClick={onOpen} title={title}>
        {title}
      </button>
      <span className="work-row-customer">
        <CustomerChip name={customer} />
      </span>
      <time className="work-row-when" dateTime={at}>
        {shortTime(at)}
      </time>
      <div className="work-row-action">{children}</div>
    </div>
  );
}

function DecisionRow({
  item,
  customer,
  navigate,
  seeWhy,
  onDecided,
}: {
  item: DecisionItem;
  customer?: string;
  navigate: (to: string) => void;
  seeWhy: () => void;
  onDecided: () => void;
}) {
  const { busy, error, result, decide } = useDecision(item, onDecided);
  return (
    <WorkRow
      kind={result ? "done" : "need"}
      number={item.number}
      onNumber={() => navigate(`order/${item.orderId}`)}
      title={error ? `${item.title} · ${error}` : item.title}
      customer={customer}
      at={item.at}
      cloud={!result}
      onOpen={seeWhy}
    >
      {result ? (
        <span className="work-row-result" role="status">
          {result}
        </span>
      ) : (
        <button
          className="primary"
          disabled={busy}
          title={decisionLabel(item)}
          onClick={() => void decide("approve")}
        >
          {busy ? "Saving…" : "Approve"}
        </button>
      )}
    </WorkRow>
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
