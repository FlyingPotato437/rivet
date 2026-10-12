import { useEffect, useId, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  X,
  Pause,
  Play,
  IconContext,
} from "@phosphor-icons/react";
import { Mark } from "./ui";
import { RivetField } from "./LandingMotion";
import { usePublicMotion } from "./usePublicMotion";

export function PublicHeader({ booking = false }: { booking?: boolean }) {
  return (
    <header className="public-header">
      <a className="public-brand" href="/" aria-label="Rivet home">
        <Mark />
        <span>
          rivet<span>.</span>
        </span>
      </a>
      <span className="public-header-note">
        Order coordination for custom equipment
      </span>
      <a className="public-header-cta" href={booking ? "/#workflow" : "/pilot"}>
        {booking ? "Explore the product" : "Request a pilot"}
        <ArrowUpRight size={17} />
      </a>
    </header>
  );
}
export function PublicFooter() {
  return (
    <footer className="public-footer public-wrap">
      <a className="public-brand" href="/" aria-label="Rivet home">
        <Mark />
        <span>
          rivet<span>.</span>
        </span>
      </a>
      <p>Built around the work between revisions.</p>
      <span>© {new Date().getFullYear()} Rivet</span>
    </footer>
  );
}

const steps = [
  {
    label: "A drawing comes back",
    description:
      "Forward the email or drop in the returned PDF. Keep the customer’s process exactly as it is.",
  },
  {
    label: "The review is prepared",
    description:
      "Read the comment log, inspect revision flags, and open the source in one click. Unclear details stay marked for review.",
  },
  {
    label: "Your team moves it forward",
    description:
      "Review the responses, export the log, and give engineering a clear drafting list. People approve what leaves the team.",
  },
];
const sampleComments = [
  {
    id: "07",
    text: "Confirm the cable entry location.",
    sheet: "E-10",
    flag: "Needs confirmation",
    tone: "neutral",
    detail:
      "The markup is on page 2 but mentions drawing E-10. Confirm the drawing link before passing this to engineering.",
    source: "Returned PDF · page 2 · Rev C",
    previous: "Drawing reference has not been confirmed.",
  },
  {
    id: "08",
    text: "Increase the enclosure depth to 36 in.",
    sheet: "E-04",
    flag: "After approval",
    tone: "amber",
    detail:
      "This request arrived after the recorded Rev B approval. Review the impact with engineering before confirming it to the customer.",
    source: "Returned PDF · page 4 · Rev C",
    previous: "Rev B approval recorded · enclosure depth 30 in.",
  },
  {
    id: "09",
    text: "Add a spare feeder in section 3.",
    sheet: "E-06",
    flag: "Scope review",
    tone: "lavender",
    detail:
      "A new feeder may affect the agreed scope. The PM needs to check the purchase order and commercial impact. Rivet has not determined that a change order is required.",
    source: "Returned PDF · page 6 · Rev C",
    previous: "New request · commercial review required.",
  },
];
function FileStamp({ format }: { format: "PDF" | "XLS" | "EML" }) {
  return (
    <span
      className={`public-file-stamp format-${format.toLowerCase()}`}
      aria-hidden="true"
    >
      <span>{format}</span>
    </span>
  );
}

function ProcessTrack() {
  return (
    <div
      className="public-process-track public-wrap"
      aria-label="From returned documents to reviewed outputs"
    >
      <svg
        className="public-process-connector"
        viewBox="0 0 1000 4"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path d="M0 2H1000" stroke="currentColor" strokeOpacity=".18" />
        <path
          className="public-process-line"
          d="M0 2H1000"
          stroke="currentColor"
          pathLength="1000"
          strokeDasharray="1000"
        />
      </svg>
      <div className="public-process-node">
        <span className="public-process-index">01</span>
        <span>
          Returned drawings<span>Start with your PDF or email</span>
        </span>
      </div>
      <div className="public-process-node">
        <span className="public-process-index">02</span>
        <span>
          Linked comments<span>Every flag leads to its source</span>
        </span>
      </div>
      <div className="public-process-node">
        <span className="public-process-index">03</span>
        <span>
          Reviewed outputs<span>Your team approves the handoff</span>
        </span>
      </div>
    </div>
  );
}

function DrawingVisual({
  focus = 0,
  compact = false,
}: {
  focus?: number;
  compact?: boolean;
}) {
  const gridId = useId();
  return (
    <svg
      className={`public-drawing drawing-focus-${focus}`}
      viewBox={compact ? "28 28 450 220" : "0 0 500 300"}
      role="img"
      aria-label={`Illustrative switchgear drawing: ${["cable entry location", "enclosure depth change", "additional spare feeder"][focus]}`}
    >
      <defs>
        <pattern
          id={gridId}
          width="12"
          height="12"
          patternUnits="userSpaceOnUse"
        >
          <path
            d="M12 0H0V12"
            fill="none"
            stroke="currentColor"
            strokeWidth=".25"
            opacity=".22"
          />
        </pattern>
      </defs>
      <rect width="500" height="300" fill={`url(#${gridId})`} />
      <g stroke="currentColor" fill="none" strokeWidth="1.1">
        <path d="M38 38H424V238H38zM38 218H424M134 38V218M231 38V218M327 38V218" />
        {[53, 150, 246, 343].map((x) => (
          <g key={x}>
            <rect
              x={x}
              y="58"
              width="65"
              height="57"
              rx="1"
              fill="currentColor"
              fillOpacity=".025"
            />
            <path
              d={`M${x + 8} 98h49m-49-8h49M${x + 13} 132h40v65h-40zM${x + 17} 149h32m-32 10h32m-32 10h32M${x + 54} 151v15`}
            />
            <circle cx={x + 32} cy="75" r="6" />
            <path d={`M${x + 32} 69v12m-6-6h12M${x + 8} 228h49`} />
          </g>
        ))}
        <path
          d="M38 20H424M38 16v8M424 16v8M450 38v200M446 38h8M446 238h8"
          opacity=".6"
        />
        <rect x="16" y="258" width="468" height="29" />
        <path d="M327 258v29M400 258v29" />
      </g>
      <g fill="currentColor" fontSize="8" fontFamily="monospace">
        <text x="184" y="15">
          GENERAL ARRANGEMENT
        </text>
        <text x="26" y="276">
          LV SWITCHGEAR / RETURNED SUBMITTAL
        </text>
        <text x="338" y="276">
          E-{focus === 0 ? "10" : focus === 1 ? "04" : "06"}
        </text>
        <text x="414" y="276">
          REV C
        </text>
        <text x="459" y="135" transform="rotate(90 459 135)">
          FRONT ELEVATION
        </text>
      </g>
      <g
        className="public-drawing-mark"
        fill="none"
        stroke="var(--drawing-ink)"
        strokeWidth="2"
      >
        {focus === 0 ? (
          <>
            <path d="M52 208q-10-9 0-15q-6-11 7-12q4-12 15-5q10-8 16 1q13-3 15 9q11 4 3 15q4 12-10 12q-5 11-17 4q-13 8-16-4q-10 3-13-5Z" />
            <path d="M112 191l29-44h56" />
            <circle cx="212" cy="147" r="13" />
            <text
              x="205"
              y="151"
              fontSize="10"
              fill="var(--drawing-ink)"
              stroke="none"
            >
              07
            </text>
          </>
        ) : focus === 1 ? (
          <>
            <path d="M429 40h40v200h-40M467 137h-38" strokeDasharray="4 3" />
            <circle cx="414" cy="137" r="13" />
            <text
              x="407"
              y="141"
              fontSize="10"
              fill="var(--drawing-ink)"
              stroke="none"
            >
              08
            </text>
          </>
        ) : (
          <>
            <rect
              x="255"
              y="127"
              width="49"
              height="76"
              strokeDasharray="4 3"
            />
            <path d="M282 127V109h-50" />
            <circle cx="217" cy="109" r="13" />
            <text
              x="210"
              y="113"
              fontSize="10"
              fill="var(--drawing-ink)"
              stroke="none"
            >
              09
            </text>
          </>
        )}
      </g>
      <rect
        className="public-drawing-scan"
        x="18"
        y="35"
        width="450"
        height="3"
        fill="var(--drawing-ink)"
        opacity=".28"
      />
    </svg>
  );
}
function WorkflowDemo() {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(
    () =>
      !matchMedia("(prefers-reduced-motion: reduce), (max-width: 800px)")
        .matches,
  );
  const [selected, setSelected] = useState<number | null>(null);
  const [output, setOutput] = useState(0);
  const [preview, setPreview] = useState(0);
  const view = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const preference = matchMedia(
      "(prefers-reduced-motion: reduce), (max-width: 800px)",
    );
    const update = () => {
      if (preference.matches) setPlaying(false);
    };
    preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { threshold: 0.35 },
    );
    if (view.current) observer.observe(view.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (
      !playing ||
      !visible ||
      matchMedia("(prefers-reduced-motion: reduce)").matches
    )
      return;
    const timer = setInterval(() => {
      if (document.hidden) return;
      setSelected(null);
      setStep((s) => (s + 1) % steps.length);
    }, 6500);
    return () => clearInterval(timer);
  }, [playing, visible]);
  const change = (index: number) => {
    setStep(index);
    setPlaying(false);
    setSelected(null);
  };
  const closeSource = () => {
    const index = selected;
    setSelected(null);
    requestAnimationFrame(() =>
      document.getElementById(`demo-comment-${index}`)?.focus(),
    );
  };
  return (
    <div
      className="public-demo"
      ref={view}
      data-playing={playing}
      onFocusCapture={(event) => {
        if (!(event.target as HTMLElement).closest(".public-demo-play"))
          setPlaying(false);
      }}
    >
      <div className="public-demo-rail">
        <p className="public-eyebrow">The daily workflow</p>
        <div
          className="public-step-list"
          role="tablist"
          aria-label="Workflow demonstration"
        >
          {steps.map((item, i) => (
            <button
              key={item.label}
              id={`workflow-step-${i}`}
              role="tab"
              aria-selected={step === i}
              aria-controls="workflow-panel"
              tabIndex={step === i ? 0 : -1}
              onClick={() => change(i)}
              onKeyDown={(e) => {
                if (
                  ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft"].includes(
                    e.key,
                  )
                ) {
                  e.preventDefault();
                  const next =
                    (i +
                      (["ArrowDown", "ArrowRight"].includes(e.key) ? 1 : 2)) %
                    3;
                  change(next);
                  document.getElementById(`workflow-step-${next}`)?.focus();
                }
              }}
              className={step === i ? "is-active" : ""}
            >
              <span className="public-step-number">0{i + 1}</span>
              <span>
                <strong>{item.label}</strong>
                <span className="public-step-description">
                  {item.description}
                </span>
              </span>
            </button>
          ))}
        </div>
        <div className="public-demo-control">
          <span>Illustrative workflow</span>
          <button
            className="public-demo-play"
            aria-label={playing ? "Pause demonstration" : "Play demonstration"}
            onClick={() => setPlaying(!playing)}
          >
            {playing ? <Pause size={14} /> : <Play size={14} />}
          </button>
        </div>
      </div>
      <div
        className="public-demo-canvas"
        role="tabpanel"
        id="workflow-panel"
        aria-labelledby={`workflow-step-${step}`}
      >
        <div className="public-window-bar">
          <span>
            <Mark small />{" "}
            {step === 0
              ? "Incoming"
              : step === 1
                ? "Prepared for review"
                : "Ready for your team"}
          </span>
          <span>
            Job 24-118 <span className="public-revision-label">Rev C</span>
          </span>
        </div>
        <div className="public-demo-scene" key={step} inert={selected !== null}>
          {step === 0 && (
            <div className="public-mail-scene">
              <div className="public-mail-heading">
                <span className="public-avatar">JL</span>
                <div>
                  <strong>
                    Jordan Lee <span>Customer engineering</span>
                  </strong>
                  <p>To: Project team</p>
                </div>
                <span>09:41</span>
              </div>
              <h3>24-118 · Returned submittal · Rev C</h3>
              <p>
                Please see our comments on the attached drawing set. Revise and
                resubmit for approval.
              </p>
              <div className="public-pdf-attachment">
                <div>
                  <FileStamp format="PDF" />
                  <span>24-118_Rev-C_returned.pdf</span>
                  <span>12 pages</span>
                </div>
                <DrawingVisual />
              </div>
              <div className="public-forward-line">
                <span className="public-forward-trace" aria-hidden="true" />
                <span>Forward to your Rivet intake address</span>
                <Check size={16} />
              </div>
            </div>
          )}
          {step === 1 && (
            <div className="public-review-scene">
              <div className="public-review-title">
                <div>
                  <h3>
                    Review comments.
                    <br /> Keep the source in view.
                  </h3>
                </div>
                <span className="public-ai-label">
                  <Mark small /> Prepared by Rivet
                </span>
              </div>
              <div className="public-review-layout">
                <div className="public-comment-rows">
                  {sampleComments.map((c, i) => (
                    <button
                      key={c.id}
                      id={`demo-comment-${i}`}
                      className={preview === i ? "is-previewed" : ""}
                      onMouseEnter={() => {
                        setPreview(i);
                        setPlaying(false);
                      }}
                      onFocus={() => setPreview(i)}
                      onClick={() => {
                        setSelected(i);
                        setPlaying(false);
                      }}
                    >
                      <span className="public-comment-number">{c.id}</span>
                      <span>
                        <strong>{c.text}</strong>
                        <span className="public-comment-meta">
                          Drawing {c.sheet}{" "}
                          <span className={`public-flag ${c.tone}`}>
                            {c.flag}
                          </span>
                        </span>
                      </span>
                      <ArrowUpRight size={17} />
                    </button>
                  ))}
                </div>
                <aside
                  className="public-linked-source"
                  aria-label="Drawing source preview"
                >
                  <div className="public-linked-source-label">
                    <span>Markup p. {[2, 4, 6][preview]}</span>
                    <span>Refers to {sampleComments[preview].sheet}</span>
                  </div>
                  <DrawingVisual key={preview} focus={preview} compact />
                  <div className="public-linked-source-note">
                    <span className="public-reference-mark">
                      {sampleComments[preview].id}
                    </span>
                    <p>{sampleComments[preview].previous}</p>
                  </div>
                </aside>
              </div>
              <p className="public-demo-hint">
                Select a comment for its source and review context.
              </p>
            </div>
          )}
          {step === 2 && (
            <div className="public-output-scene">
              <div
                className="public-output-tabs"
                role="tablist"
                aria-label="Output preview"
              >
                {["Response letter", "Excel log", "For drafting"].map(
                  (name, i) => (
                    <button
                      key={name}
                      role="tab"
                      id={`output-step-${i}`}
                      aria-controls="output-panel"
                      tabIndex={output === i ? 0 : -1}
                      onKeyDown={(event) => {
                        const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
                        if (!keys.includes(event.key)) return;
                        event.preventDefault();
                        const next =
                          event.key === "Home"
                            ? 0
                            : event.key === "End"
                              ? 2
                              : (i + (event.key === "ArrowRight" ? 1 : 2)) % 3;
                        setOutput(next);
                        setPlaying(false);
                        document.getElementById(`output-step-${next}`)?.focus();
                      }}
                      aria-selected={output === i}
                      onClick={() => {
                        setOutput(i);
                        setPlaying(false);
                      }}
                    >
                      {name}
                    </button>
                  ),
                )}
              </div>
              <div
                className="public-output-page"
                role="tabpanel"
                id="output-panel"
                aria-labelledby={`output-step-${output}`}
                key={output}
              >
                {output === 0 ? (
                  <>
                    <span className="public-document-label">
                      DRAFT / FOR PM REVIEW
                    </span>
                    <h3>Response to comments</h3>
                    <p>Job 24-118 · Low-voltage switchgear · Rev C</p>
                    <hr />
                    <h4>07 — Cable entry location</h4>
                    <p>
                      Please confirm that this comment refers to drawing E-10.
                      We will route it to engineering once the location is
                      confirmed.
                    </p>
                    <h4>08 — Enclosure depth</h4>
                    <p>
                      Engineering and the PM need to review the requested change
                      against the previously approved revision.
                    </p>
                    <div className="public-review-required">
                      <span className="public-review-dot" aria-hidden="true" />{" "}
                      Your review is required before sending.
                    </div>
                  </>
                ) : output === 1 ? (
                  <>
                    <span className="public-document-label">
                      EXCEL / COMMENT LOG
                    </span>
                    <h3>Comment resolution log</h3>
                    <table>
                      <thead>
                        <tr>
                          <th>ID</th>
                          <th>Comment</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sampleComments.map((c) => (
                          <tr key={c.id}>
                            <td>{c.id}</td>
                            <td>{c.text}</td>
                            <td>Needs response</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p>
                      Authors, timestamps, source pages, and recorded responses
                      stay attached to each comment.
                    </p>
                  </>
                ) : (
                  <>
                    <span className="public-document-label">
                      ENGINEERING / DRAFTING LIST
                    </span>
                    <h3>Drafting handoff</h3>
                    <div className="public-drafting-row">
                      <span>01</span>
                      <div>
                        <strong>E-04 · Enclosure depth</strong>
                        <p>
                          Review 36 in. request against the approved 30 in.
                          arrangement.
                        </p>
                      </div>
                    </div>
                    <div className="public-drafting-row">
                      <span>02</span>
                      <div>
                        <strong>E-06 · Spare feeder</strong>
                        <p>
                          Await PM scope review before changing the drawing.
                        </p>
                      </div>
                    </div>
                    <p className="public-document-note">
                      Example handoff · engineering determines the design.
                    </p>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
        {selected !== null && (
          <div
            className="public-evidence-preview"
            role="dialog"
            aria-modal="false"
            aria-label={`Source for comment ${sampleComments[selected].id}`}
            onKeyDown={(e) => {
              if (e.key === "Escape") closeSource();
            }}
          >
            <div className="public-evidence-header">
              <span>
                Comment {sampleComments[selected].id} · Source & context
              </span>
              <button
                aria-label="Close source preview"
                onClick={closeSource}
                autoFocus
              >
                <X size={20} />
              </button>
            </div>
            <span className={`public-flag ${sampleComments[selected].tone}`}>
              {sampleComments[selected].flag}
            </span>
            <h3>{sampleComments[selected].text}</h3>
            <p>{sampleComments[selected].detail}</p>
            <div className="public-source-excerpt">
              <FileStamp format="PDF" />
              <div>
                <strong>{sampleComments[selected].source}</strong>
                <p>{sampleComments[selected].previous}</p>
              </div>
            </div>
            <p className="public-demo-hint">
              Illustrative data. Flags support review; they are not engineering
              or commercial decisions.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export function Landing() {
  const root = useRef<HTMLDivElement>(null);
  usePublicMotion(root);
  return (
    <IconContext.Provider value={{ weight: "regular", "aria-hidden": true }}>
      <div className="rivet-public" ref={root}>
        <a className="public-skip" href="#main">
          Skip to content
        </a>
        <PublicHeader />
        <main id="main">
          <section className="public-hero public-wrap">
            <div className="public-hero-grid">
              <div className="public-hero-copy">
                <p className="public-eyebrow">
                  <span className="public-live-dot" /> For custom equipment
                  teams
                </p>
                <h1>
                  Returned drawings.
                  <br />
                  <span>Ready for review.</span>
                </h1>
                <div className="public-hero-bottom">
                  <p>
                    Rivet prepares the comment log, flags revision risks, and
                    organizes the next handoff. Your team makes the decisions.
                  </p>
                  <a className="public-text-link" href="#workflow">
                    See how it works <ArrowDown size={19} />
                  </a>
                </div>
              </div>
              <div
                className="public-hero-field"
                aria-label="Rivet’s animated pixel mark"
              >
                <RivetField />
                <div className="public-field-coordinates" aria-hidden="true">
                  <span>INPUT → CONTEXT → REVIEW</span>
                  <span>R / 01</span>
                </div>
              </div>
            </div>
          </section>
          <ProcessTrack />
          <section id="workflow" className="public-workflow public-wrap">
            <div className="public-section-heading" data-public-reveal>
              <p className="public-eyebrow">
                From the inbox to the next revision
              </p>
              <h2>
                A returned drawing.
                <br />
                <span>A clear way forward.</span>
              </h2>
              <p>
                Keep the email. Keep the spreadsheet.
                <br />
                Let Rivet prepare the work between them.
              </p>
            </div>
            <div data-public-reveal>
              <WorkflowDemo />
            </div>
          </section>
          <section className="public-inbox-section">
            <div className="public-inbox-layout public-wrap">
              <div className="public-inbox-copy" data-public-reveal>
                <p className="public-eyebrow">Built around the PM</p>
                <h2>
                  The important details.
                  <br />
                  <span>Where you already work.</span>
                </h2>
                <p>
                  Get a concise revision brief, with the comments that need a
                  decision and links to the full record. Open the workspace when
                  you need the drawing, the history, or the handoff.
                </p>
                <div className="public-capability-line">
                  <FileStamp format="EML" />
                  <span>Forward an email or upload a PDF</span>
                </div>
                <div className="public-capability-line">
                  <FileStamp format="XLS" />
                  <span>Export the spreadsheet your team already uses</span>
                </div>
                <p className="public-integration-note">
                  Forwarding is configured during pilot setup. Outlook,
                  SharePoint, and ERP connections are planned; they are not
                  required to start.
                </p>
              </div>
              <div className="public-digest">
                <div className="public-digest-sender">
                  <span className="public-agent-avatar">
                    <Mark />
                  </span>
                  <div>
                    <strong>Rivet</strong>
                    <p>Revision brief · example</p>
                  </div>
                  <span>10:04</span>
                </div>
                <h3>Rev C is ready for review.</h3>
                <p className="public-digest-subject">
                  24-118 / Switchgear submittal
                </p>
                <div className="public-digest-numbers">
                  <div>
                    <strong>14</strong>
                    <span>comments</span>
                  </div>
                  <div>
                    <strong>3</strong>
                    <span>need a decision</span>
                  </div>
                </div>
                <div className="public-digest-alert">
                  <span
                    className="public-reference-mark amber"
                    aria-hidden="true"
                  >
                    08
                  </span>
                  <div>
                    <strong>Enclosure depth changed after approval</strong>
                    <p>
                      Review against the recorded Rev B approval before
                      confirming the request.
                    </p>
                  </div>
                </div>
                <div className="public-digest-alert lavender">
                  <span className="public-reference-mark" aria-hidden="true">
                    09
                  </span>
                  <div>
                    <strong>Spare feeder needs a scope review</strong>
                    <p>
                      The PM needs to confirm whether this affects the original
                      order.
                    </p>
                  </div>
                </div>
                <div className="public-digest-attachments">
                  <span>
                    <FileStamp format="XLS" /> Comment log
                  </span>
                  <span>
                    <FileStamp format="PDF" /> Response draft
                  </span>
                  <span>Drafting list</span>
                </div>
                <a href="#workflow" className="public-digest-link">
                  Explore the review flow <ArrowRight size={17} />
                </a>
              </div>
            </div>
          </section>
          <section className="public-start public-wrap" data-public-reveal>
            <div>
              <p className="public-eyebrow">A focused first step</p>
              <h2>
                Start with one order.
                <br />
                <span>See what it saves.</span>
              </h2>
            </div>
            <div className="public-start-details">
              <p>
                Bring a returned drawing and the revision before it. We’ll map
                your process together and test Rivet against the record your
                team already keeps.
              </p>
              <ol>
                <li>
                  <span>01</span>
                  <div>
                    <strong>Walk through your workflow</strong>
                    <p>Show us where comments and changes get lost.</p>
                  </div>
                </li>
                <li>
                  <span>02</span>
                  <div>
                    <strong>Try it on a real revision</strong>
                    <p>Use a redacted order or start with sample documents.</p>
                  </div>
                </li>
                <li>
                  <span>03</span>
                  <div>
                    <strong>Review the result with your team</strong>
                    <p>Check the evidence, the outputs, and the time saved.</p>
                  </div>
                </li>
              </ol>
            </div>
          </section>
          <section
            className="public-cta-section public-wrap"
            data-public-reveal
          >
            <p className="public-eyebrow">Private pilots now forming</p>
            <h2>
              Make the next
              <br />
              revision easier.
            </h2>
            <a className="public-button" href="/pilot">
              Request a pilot <ArrowUpRight size={20} />
            </a>
            <p>
              A 30-minute conversation with the founder.
              <br />
              No migration or confidential documents required.
            </p>
            <div className="public-cta-mark" aria-hidden="true">
              <Mark />
            </div>
          </section>
        </main>
        <PublicFooter />
      </div>
    </IconContext.Provider>
  );
}
