import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  FilePdf,
  FileXls,
  Paperclip,
  ArrowBendDownRight,
  EnvelopeSimple,
  X,
  Pause,
  Play,
  Warning,
  CheckCircle,
} from "@phosphor-icons/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { Mark } from "./ui";
import { RivetField } from "./LandingMotion";

gsap.registerPlugin(ScrollTrigger);

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
function DrawingVisual({ annotated = true }: { annotated?: boolean }) {
  return (
    <svg
      className="public-drawing"
      viewBox="0 0 500 285"
      role="img"
      aria-label="Illustrative switchgear drawing with a marked cable entry"
    >
      <defs>
        <pattern
          id="drawing-grid"
          width="20"
          height="20"
          patternUnits="userSpaceOnUse"
        >
          <path
            d="M20 0H0V20"
            fill="none"
            stroke="currentColor"
            strokeWidth=".2"
            opacity=".3"
          />
        </pattern>
      </defs>
      <rect width="500" height="285" fill="url(#drawing-grid)" />
      <g stroke="currentColor" fill="none" strokeWidth="1.2">
        <path d="M68 51H412V238H68zM68 218H412M153 51V218M240 51V218M325 51V218M81 75h58v50H81zM166 75h60v50h-60zM253 75h59v50h-59zM338 75h60v50h-60zM93 144h35v54H93zM178 144h35v54h-35zM265 144h35v54h-35zM350 144h35v54h-35zM87 100h44M172 100h48M260 100h45M344 100h47M69 30h343M68 25v10M412 25v10M438 51v188M432 51h12M432 238h12" />
        <path d="M90 232h28m57 0h28m58 0h28m57 0h28" strokeWidth="2" />
      </g>
      <g fill="currentColor" fontSize="8" fontFamily="monospace">
        <text x="206" y="23">
          GENERAL ARRANGEMENT
        </text>
        <text x="77" y="266">
          E-10 / LV SWITCHGEAR
        </text>
        <text x="347" y="266">
          REV C
        </text>
      </g>
      {annotated && (
        <g
          className="public-drawing-mark"
          fill="none"
          stroke="#cbb0f0"
          strokeWidth="2"
        >
          <path d="M82 203q-10-9 0-15q-6-11 7-12q4-12 15-5q10-8 16 1q13-3 15 9q11 4 3 15q4 12-10 12q-5 11-17 4q-13 8-16-4q-10 3-13-5Z" />
          <path d="M140 189l30-38h86" />
          <circle cx="270" cy="151" r="12" />
          <text x="265" y="155" fontSize="11" fill="#cbb0f0" stroke="none">
            07
          </text>
        </g>
      )}
    </svg>
  );
}
function WorkflowDemo() {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(
    () => !matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [selected, setSelected] = useState<number | null>(null);
  const [output, setOutput] = useState(0);
  const view = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
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
    <div className="public-demo" ref={view}>
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
        <div className="public-demo-scene" key={step}>
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
                  <FilePdf size={19} />
                  <span>24-118_Rev-C_returned.pdf</span>
                  <span>12 pages</span>
                </div>
                <DrawingVisual />
              </div>
              <div className="public-forward-line">
                <ArrowBendDownRight size={21} />
                <span>Forward to your Rivet intake address</span>
                <Check size={16} />
              </div>
            </div>
          )}
          {step === 1 && (
            <div className="public-review-scene">
              <div className="public-review-title">
                <div>
                  <p className="public-eyebrow">Revision review</p>
                  <h3>
                    Three decisions.
                    <br /> The evidence beside them.
                  </h3>
                </div>
                <span className="public-ai-label">
                  <Mark small /> Prepared by Rivet
                </span>
              </div>
              <div className="public-comment-rows">
                {sampleComments.map((c, i) => (
                  <button
                    key={c.id}
                    id={`demo-comment-${i}`}
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
              <p className="public-demo-hint">
                Select a comment to inspect its source and flag.
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
              <div className="public-output-page">
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
                      <CheckCircle size={17} /> Your review is required before
                      sending.
                    </div>
                  </>
                ) : output === 1 ? (
                  <>
                    <span className="public-document-label">
                      EXCEL / COMMENT LOG
                    </span>
                    <h3>The record travels with you.</h3>
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
                    <h3>A clear next handoff.</h3>
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
              <FilePdf size={20} />
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
  useLayoutEffect(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const ctx = gsap.context(() => {
      gsap.from(".public-hero-copy > *", {
        y: 22,
        opacity: 0,
        duration: 0.9,
        stagger: 0.12,
        ease: "power2.out",
      });
      gsap.fromTo(
        ".public-hero-image img",
        { scale: 1.05 },
        {
          scale: 1,
          ease: "none",
          scrollTrigger: {
            trigger: ".public-hero-image",
            start: "top 85%",
            end: "bottom top",
            scrub: true,
          },
        },
      );
      gsap.fromTo(
        ".public-digest",
        { y: 40, rotate: 1.5 },
        {
          y: -25,
          rotate: -1.5,
          ease: "none",
          scrollTrigger: {
            trigger: ".public-inbox-section",
            start: "top bottom",
            end: "bottom top",
            scrub: 1,
          },
        },
      );
      gsap.fromTo(
        ".public-flow-route",
        { strokeDashoffset: 1000 },
        {
          strokeDashoffset: 0,
          ease: "none",
          scrollTrigger: {
            trigger: ".public-flow-graphic",
            start: "top 90%",
            end: "bottom 30%",
            scrub: 1,
          },
        },
      );
      gsap.utils.toArray<HTMLElement>("[data-public-reveal]").forEach((el) =>
        gsap.from(el, {
          y: 30,
          opacity: 0,
          duration: 0.9,
          ease: "power2.out",
          scrollTrigger: { trigger: el, start: "top 92%", once: true },
        }),
      );
    }, root);
    return () => ctx.revert();
  }, []);
  return (
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
                <span className="public-live-dot" /> For custom equipment teams
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
          <figure className="public-hero-image">
            <img
              src="/media/switchgear-assembly.jpg"
              alt="Illustration of precision-built switchgear cabinets in a manufacturing assembly hall"
              width="1672"
              height="940"
              fetchPriority="high"
            />
            <div className="public-image-caption">
              <span>Built for the work behind custom equipment.</span>
              <span>Illustrative imagery</span>
            </div>
            <div className="public-image-cross" aria-hidden="true">
              +
            </div>
          </figure>
        </section>
        <div className="public-flow-graphic public-wrap" aria-hidden="true">
          <svg viewBox="0 0 1200 150" fill="none">
            <path
              d="M0 75H240Q270 75 285 45L300 20H450L480 75H690L720 125H870L900 75H1200"
              stroke="#31303b"
            />
            <path
              className="public-flow-route"
              d="M0 75H240Q270 75 285 45L300 20H450L480 75H690L720 125H870L900 75H1200"
              pathLength="1000"
              stroke="#c7abe8"
              strokeWidth="1.4"
              strokeDasharray="1000"
            />
            <g stroke="#96939f" strokeWidth="1.3">
              <path d="M174 52h20l8 8v33h-28zM194 52v8h8M181 70h14m-14 7h14m-14 7h9" />
              <path d="M570 54h60v42h-60zM570 55l30 22 30-22" />
              <path d="M991 64l9 10 19-22" />
              <rect x="980" y="42" width="48" height="48" rx="24" />
            </g>
            <g fill="#85818f" fontSize="10" fontFamily="monospace">
              <text x="164" y="120">
                RETURNED
              </text>
              <text x="570" y="123">
                PREPARED
              </text>
              <text x="976" y="120">
                REVIEWED
              </text>
            </g>
          </svg>
        </div>
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
                <EnvelopeSimple size={20} />
                <span>Forward an email or upload a PDF</span>
              </div>
              <div className="public-capability-line">
                <FileXls size={20} />
                <span>Export the spreadsheet your team already uses</span>
              </div>
              <p className="public-integration-note">
                Forwarding is configured during pilot setup. Outlook,
                SharePoint, and ERP connections are planned; they are not
                required to start.
              </p>
            </div>
            <div className="public-digest" data-public-reveal>
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
                <Warning size={18} />
                <div>
                  <strong>Enclosure depth changed after approval</strong>
                  <p>
                    Review against the recorded Rev B approval before confirming
                    the request.
                  </p>
                </div>
              </div>
              <div className="public-digest-alert lavender">
                <ArrowUpRight size={18} />
                <div>
                  <strong>Spare feeder needs a scope review</strong>
                  <p>
                    The PM needs to confirm whether this affects the original
                    order.
                  </p>
                </div>
              </div>
              <div className="public-digest-attachments">
                <Paperclip size={17} /> Comment log · Response draft · Drafting
                list
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
              your process together and test Rivet against the record your team
              already keeps.
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
        <section className="public-cta-section public-wrap" data-public-reveal>
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
  );
}
