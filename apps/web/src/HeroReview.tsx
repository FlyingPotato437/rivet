import { useEffect, useRef, useState } from "react";
import { ArrowRight, Pause, Play, Check } from "@phosphor-icons/react";
import { Mark, RivetIcon, type RivetIconName } from "./ui";
import "./workbench-preview.css";

const stages: {
  name: string;
  title: string;
  detail: string;
  status: string;
  icon: RivetIconName;
}[] = [
  {
    name: "Import",
    title: "The original stays attached.",
    detail:
      "The returned PDF, annotation wording, author and source page are preserved.",
    status: "Imported annotation",
    icon: "drawing",
  },
  {
    name: "Prepare",
    title: "Rivet prepares a drawing link.",
    detail:
      "The comment names E-10. Rivet finds that sheet on page 10 and prepares a proposed link.",
    status: "AI proposed · review needed",
    icon: "agent",
  },
  {
    name: "Review",
    title: "Review the work beside the evidence.",
    detail:
      "Open the annotation and the proposed drawing. The team confirms the reference and records its response.",
    status: "Awaiting human review",
    icon: "review",
  },
  {
    name: "Handoff",
    title: "A reviewed record, ready to share.",
    detail:
      "The source, response and confirmed reference remain together. Review the recipients before sending an update.",
    status: "Human-reviewed example",
    icon: "handoff",
  },
];

export function HeroReview() {
  const [stage, setStage] = useState(0);
  const [paused, setPaused] = useState(false);
  const [source, setSource] = useState(false);
  const [activity, setActivity] = useState(false);
  const [visible, setVisible] = useState(false);
  const [reduced, setReduced] = useState(
    () => matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    media.addEventListener("change", update);
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { threshold: 0.2 },
    );
    if (root.current) observer.observe(root.current);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", update);
    };
  }, []);
  useEffect(() => {
    if (paused || reduced || !visible) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) setStage((value) => (value + 1) % stages.length);
    }, 6500);
    return () => clearInterval(timer);
  }, [paused, reduced, visible]);
  const current = stages[stage];
  const chooseStage = (index: number) => {
    setStage(index);
    setPaused(true);
  };
  return (
    <div
      ref={root}
      className={`workbench-preview phase-${stage}`}
      aria-label="Interactive example of the Rivet review workbench"
    >
      <div className="wp-header">
        <div className="wp-identity">
          <Mark small />
          <span>
            rivet<span>.</span>
          </span>
        </div>
        <span className="wp-project">
          Westbridge expansion <span> / </span> Review
        </span>
        <span className="wp-example">Illustrative example</span>
      </div>
      <div className="wp-main">
        <section className="wp-document" aria-label="Sample source document">
          <div className="wp-file">
            <RivetIcon name="drawing" size={18} />
            <span>Returned submittal · Rev C.pdf</span>
            <small>p. {source ? "02" : "10"} / 16</small>
          </div>
          <div className="wp-document-tabs">
            <button
              aria-pressed={!source}
              onClick={() => {
                setSource(false);
                setPaused(true);
              }}
            >
              Drawing E-10
            </button>
            <button
              aria-pressed={source}
              onClick={() => {
                setSource(true);
                setPaused(true);
              }}
            >
              Source annotation · p. 2
            </button>
          </div>
          <div className="wp-canvas">
            <div className="wp-paper">
              <div className="wp-paper-top">
                <span>Westbridge expansion</span>
                <span>REV C</span>
              </div>
              <h3>
                {source ? "Submittal review comments" : "General arrangement"}
              </h3>
              <p className="wp-paper-sub">
                {source
                  ? "Returned by Priya Mehta · October 6"
                  : "Low-voltage switchgear · SWGR-01"}
              </p>
              {source ? (
                <div className="wp-source-letter">
                  <div className="wp-source-annotation">
                    <span>ANNOTATION 07</span>
                    <p>
                      Please confirm the cable entry location shown on drawing
                      E-10.
                    </p>
                  </div>
                  <div className="wp-source-lines" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </div>
                </div>
              ) : (
                <svg
                  className="wp-blueprint"
                  viewBox="0 0 580 320"
                  role="img"
                  aria-label="Illustrative three-section switchgear drawing with the cable-entry area highlighted; not an engineering document"
                >
                  <g fill="none" stroke="currentColor" strokeWidth="1.4">
                    <path d="M80 67h422v192H80zM90 77h402v170H90zM224 77v170M358 77v170M80 267h422M98 273v-26M484 273v-26" />
                    {[108, 242, 376].map((x) => (
                      <g key={x}>
                        <rect x={x} y="94" width="94" height="108" rx="2" />
                        <rect x={x + 24} y="108" width="46" height="27" />
                        <path
                          d={`M${x + 47} 145v39m-12-12 12 12 12-12M${x + 16} 214h62M${x + 16} 222h62M${x + 16} 230h62`}
                        />
                        <circle cx={x + 81} cy="152" r="3" />
                      </g>
                    ))}
                    <path
                      d="M80 53V35M502 53V35M80 42h422M86 36l-12 12M508 36l-12 12"
                      opacity=".55"
                    />
                  </g>
                  <rect
                    className="wp-drawing-mark"
                    x="363"
                    y="231"
                    width="100"
                    height="43"
                    rx="2"
                  />
                  <path className="wp-drawing-leader" d="M463 251h61v-49" />
                  <text
                    className="wp-sheet-label"
                    x="288"
                    y="307"
                    textAnchor="middle"
                  >
                    FRONT ELEVATION · E-10
                  </text>
                </svg>
              )}
              <div className="wp-paper-bottom">
                <span>
                  {source
                    ? "Original annotation retained"
                    : "Illustrative drawing · not for construction"}
                </span>
                <span>PAGE {source ? "02" : "10"}</span>
              </div>
            </div>
          </div>
          <div className="wp-evidence-trail">
            <RivetIcon name="evidence" size={17} />
            <span>Comment 07</span>
            <ArrowRight size={13} />
            <span>Source p. 2</span>
            <ArrowRight size={13} />
            <strong>E-10 · p. 10</strong>
          </div>
        </section>
        <aside className="wp-inspector" aria-label="Sample record inspector">
          <div className="wp-inspector-tabs">
            <button
              aria-pressed={!activity}
              onClick={() => {
                setActivity(false);
                setPaused(true);
              }}
            >
              Record
            </button>
            <button
              aria-pressed={activity}
              onClick={() => {
                setActivity(true);
                setPaused(true);
              }}
            >
              <RivetIcon name="agent" size={16} />
              Rivet activity
            </button>
          </div>
          {activity ? (
            <div className="wp-activity">
              <span className="wp-origin">Illustrative run · sample data</span>
              <h4>Prepare returned comments</h4>
              <ol>
                {[
                  "Read returned document",
                  "Extract annotation records",
                  "Find drawing references",
                  "Prepare a reviewable link",
                ].map((label, index) => (
                  <li
                    key={label}
                    className={index <= stage ? "complete" : "pending"}
                  >
                    {index <= stage ? (
                      <Check size={16} />
                    ) : (
                      <span className="wp-step-dot" />
                    )}
                    <span>{label}</span>
                  </li>
                ))}
              </ol>
              <p>
                No messages sent. Engineering decisions remain with the team.
              </p>
            </div>
          ) : (
            <div className="wp-record">
              <div className="wp-record-number">
                <span>COMMENT 07</span>
                <span>{stage === 3 ? "Reviewed" : "Needs review"}</span>
              </div>
              <h4>
                Please confirm the cable entry location shown on drawing E-10.
              </h4>
              <div className="wp-author">
                <span>PM</span>Priya Mehta · Oct 6
              </div>
              <div className="wp-origin">
                <RivetIcon name="drawing" size={15} />
                Imported from PDF annotation
              </div>
              <div className="wp-field">
                <span>Referenced drawing</span>
                <button
                  onClick={() => {
                    setSource(false);
                    setPaused(true);
                  }}
                >
                  <RivetIcon name="evidence" size={18} />
                  <span>
                    E-10 · page 10
                    <small>
                      {stage === 3
                        ? "Reviewed by the team · example"
                        : "AI-proposed match · example"}
                    </small>
                  </span>
                  <ArrowRight size={17} />
                </button>
              </div>
              <div
                className={`wp-stage-note ${stage === 3 ? "reviewed" : ""}`}
                key={stage}
              >
                <span>
                  <RivetIcon name={current.icon} size={17} />
                  {current.status}
                </span>
                <strong>{current.title}</strong>
                <p>{current.detail}</p>
              </div>
            </div>
          )}
        </aside>
      </div>
      <div className="wp-playback">
        <div role="group" aria-label="Example stages">
          {stages.map((item, index) => (
            <button
              key={item.name}
              aria-pressed={stage === index}
              onClick={() => chooseStage(index)}
            >
              <span>0{index + 1}</span>
              {item.name}
            </button>
          ))}
        </div>
        <button
          className="wp-pause"
          aria-label={paused || reduced ? "Play example" : "Pause example"}
          disabled={reduced}
          onClick={() => setPaused(!paused)}
        >
          {paused || reduced ? <Play size={15} /> : <Pause size={15} />}
        </button>
      </div>
    </div>
  );
}
