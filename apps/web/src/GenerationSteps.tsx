import { useEffect, useState } from "react";
import {
  CheckCircle,
  CircleNotch,
  Circle,
  WarningCircle,
} from "@phosphor-icons/react";
import type { ConnectedContext } from "./ConnectedSources";

// One reply request, as observed by the pane. Every step is marked done only when the
// request that performs it has actually returned; nothing here is a timed animation.
export type GenerationRun = {
  started: number;
  email?: { subject: string; sender: string } | null;
  emailAt?: number;
  systems: ConnectedContext["systems"];
  sourcesAt?: number;
  connected?: ConnectedContext | null;
  coverage?: {
    documents_read: number;
    documents_total?: number;
    comments_considered: number;
    source_excerpts?: number;
  };
  model?: string;
  finishedAt?: number;
  outcome?: string;
  citations?: number;
  unresolved?: number;
  failed?: boolean;
};

type State = "wait" | "run" | "done" | "fail";

function seconds(ms: number) {
  return ms < 10000 ? (ms / 1000).toFixed(1) : String(Math.round(ms / 1000));
}

function clock(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
      });
}

function modeLabel(system: ConnectedContext["systems"][number]) {
  if (system.mode === "live")
    return system.kind === "sharepoint"
      ? "Live · Microsoft Graph"
      : /\bSAP\b/.test(system.label)
        ? "Live · SAP API"
        : "Live connection";
  return system.mode === "local_demo" ? "Local demo service" : system.mode;
}

function Icon({ state }: { state: State }) {
  if (state === "done")
    return <CheckCircle size={18} weight="fill" className="gs-done" />;
  if (state === "run") return <CircleNotch size={18} className="gs-spin" />;
  if (state === "fail")
    return <WarningCircle size={18} weight="fill" className="gs-fail" />;
  return <Circle size={18} className="gs-wait" />;
}

function Step({
  state,
  title,
  detail,
  children,
}: {
  state: State;
  title: string;
  detail?: string;
  children?: React.ReactNode;
}) {
  return (
    <li className={`gs-step gs-${state}`}>
      <Icon state={state} />
      <div>
        <strong>{title}</strong>
        {detail && <p>{detail}</p>}
        {children}
      </div>
    </li>
  );
}

export function GenerationSteps({ run }: { run: GenerationRun }) {
  const [now, setNow] = useState(Date.now());
  const running = !run.finishedAt && !run.failed;
  useEffect(() => {
    setNow(Date.now());
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 200);
    return () => window.clearInterval(timer);
  }, [running, run.started]);
  // A reused component can hold a tick from an earlier request; never show negative time.
  const tick = Math.max(now, run.started);
  const end = run.finishedAt || tick;
  const fail = (s: State): State => (run.failed && s === "run" ? "fail" : s);
  const emailState: State = run.emailAt ? "done" : "run";
  const sourcesState: State = run.sourcesAt
    ? "done"
    : run.emailAt
      ? "run"
      : "wait";
  const ok = run.outcome === "ready" || run.outcome === "approved";
  const writing: State = run.finishedAt
    ? ok
      ? "done"
      : "fail"
    : run.sourcesAt
      ? "run"
      : "wait";
  const findings = run.connected?.findings || [];
  const systems = run.connected?.systems?.length
    ? run.connected.systems
    : run.systems;
  return (
    <section className="gs" role="status" aria-live="polite">
      <header>
        <span>
          {running ? "Preparing your response" : "How Rivet prepared it"}
        </span>
        <span className="gs-time">
          {seconds(end - run.started)} s{running ? "" : " total"}
        </span>
      </header>
      <ol>
        {run.email !== null && (
          <Step
            state={fail(emailState)}
            title="Read the customer email"
            detail={
              run.email
                ? `${run.email.subject}${run.email.sender ? ` · ${run.email.sender}` : ""}`
                : "Matching the selected Outlook message"
            }
          />
        )}
        {systems.length > 0 && (
          <Step
            state={fail(sourcesState)}
            title={
              sourcesState === "done"
                ? "Checked connected systems"
                : "Checking connected systems"
            }
            detail={
              run.connected?.checked_at
                ? `Fresh read at ${clock(run.connected.checked_at)}`
                : "Fresh read before this reply"
            }
          >
            <ul className="gs-systems">
              {systems.map((s) => (
                <li key={s.key}>
                  <Icon state={fail(sourcesState)} />
                  <span>
                    {s.label}
                    <em>{modeLabel(s)}</em>
                  </span>
                  {sourcesState === "done" && (
                    <b>
                      {s.documents} file{s.documents === 1 ? "" : "s"}
                    </b>
                  )}
                </li>
              ))}
            </ul>
          </Step>
        )}
        {systems.length > 0 && (
          <Step
            state={fail(sourcesState === "done" ? "done" : "wait")}
            title={
              sourcesState === "done"
                ? findings.length
                  ? `Found ${findings.length} thing${findings.length === 1 ? "" : "s"} to resolve`
                  : "No source conflicts recorded"
                : "Compare the sources"
            }
          >
            {sourcesState === "done" && findings.length > 0 && (
              <ul className="gs-findings">
                {findings.map((f) => (
                  <li key={f.title}>
                    <strong>{f.title}</strong>
                    {f.kind === "source_conflict" && (
                      <span>
                        {f.detail.replace(/\. Resolve before.*$/, "")}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Step>
        )}
        <Step
          state={fail(run.sourcesAt ? "done" : "wait")}
          title="Select the evidence"
          detail={
            run.coverage
              ? `${run.coverage.source_excerpts ?? 0} excerpts from ${run.coverage.documents_read}${run.coverage.documents_total ? ` of ${run.coverage.documents_total}` : ""} documents · ${run.coverage.comments_considered} comments`
              : undefined
          }
        />
        <Step
          state={fail(writing)}
          title={writing === "done" ? "Wrote the reply" : "Write the reply"}
          detail={
            writing === "run"
              ? `AI${run.model ? ` · ${run.model}` : ""} · ${seconds(Math.max(0, tick - (run.sourcesAt || tick)))} s`
              : writing === "done" && run.sourcesAt && run.finishedAt
                ? `AI${run.model ? ` · ${run.model}` : ""} · ${seconds(run.finishedAt - run.sourcesAt)} s`
                : undefined
          }
        />
        <Step
          state={
            run.finishedAt
              ? ok
                ? "done"
                : "fail"
              : run.failed
                ? "fail"
                : "wait"
          }
          title="Check citation references"
          detail={
            run.finishedAt && ok
              ? `${run.citations ?? 0} citation references checked against the sources${run.unresolved ? ` · ${run.unresolved} points need confirmation` : ""}`
              : undefined
          }
        />
      </ol>
      {running && (
        <p className="gs-note">No email is being sent. You review it first.</p>
      )}
    </section>
  );
}
