import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  FilePdf,
  LinkSimple,
  Pause,
  Play,
  WarningCircle,
} from "@phosphor-icons/react";

const stages = [
  {
    name: "Receive",
    title: "A customer returns a marked-up drawing.",
    detail: "The original PDF stays attached to the order.",
    status: "Customer markup",
    icon: FilePdf,
  },
  {
    name: "Capture",
    title: "The comment becomes a traceable record.",
    detail: "Original wording, author, page and revision stay together.",
    status: "Comment captured",
    icon: LinkSimple,
  },
  {
    name: "Review",
    title: "An unclear drawing reference needs a person.",
    detail: "The markup is on page 2. It refers to drawing E-10.",
    status: "Confirm location",
    icon: WarningCircle,
  },
  {
    name: "Respond",
    title: "The team records its response.",
    detail: "A response stays connected to the comment and its source.",
    status: "Response recorded",
    icon: Check,
  },
];

export function HeroReview() {
  const [stage, setStage] = useState(0);
  const [paused, setPaused] = useState(false);
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
    }, 5500);
    return () => clearInterval(timer);
  }, [paused, reduced, visible]);
  const current = stages[stage];
  const StageIcon = current.icon;
  return (
    <div
      ref={root}
      className={`hero-review hero-review-stage-${stage}`}
      aria-label="Interactive example of Rivet's comment workflow"
    >
      <div className="hero-review-top">
        <span>
          <i /> Order 2418 · Switchboard
        </span>
        <span>Illustrative example</span>
      </div>
      <div className="hero-drawing">
        <div className="hero-drawing-label">
          <FilePdf size={16} />
          <span>
            Customer submittal<span>Returned with comments · Rev B</span>
          </span>
          <b>02</b>
        </div>
        <svg
          className="hero-blueprint"
          viewBox="0 0 640 330"
          role="img"
          aria-label="Switchboard drawing with a comment linked to the cable entry area"
        >
          <defs>
            <pattern
              id="rivet-grid"
              width="24"
              height="24"
              patternUnits="userSpaceOnUse"
            >
              <path
                d="M24 0H0V24"
                fill="none"
                stroke="currentColor"
                strokeWidth="0.35"
              />
            </pattern>
          </defs>
          <rect
            width="640"
            height="330"
            fill="url(#rivet-grid)"
            opacity=".25"
          />
          <g
            className="hero-board"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.2"
          >
            <path d="M126 78H505V272H126Z M136 87H495V260H136Z M252 87V260 M374 87V260 M126 279H505 M137 284V272 M494 284V272" />
            {[148, 270, 392].map((x) => (
              <g key={x}>
                <rect x={x} y="105" width="80" height="108" rx="2" />
                <rect x={x + 20} y="121" width="40" height="24" rx="2" />
                <path
                  d={`M${x + 40} 150v38m-12-12 12 12 12-12 M${x + 14} 226h52 M${x + 14} 233h52 M${x + 14} 240h52`}
                />
                <circle cx={x + 69} cy="159" r="2.5" />
              </g>
            ))}
            <path
              d="M116 78H98M116 272H98M103 78V272M98 84L108 74M98 278L108 268 M126 63V48M505 63V48M126 54H505M132 49L120 59M511 49L499 59"
              opacity=".5"
            />
            <path
              className="hero-cable"
              d="M433 275V299H550V210"
              strokeWidth="2"
            />
          </g>
          <g
            className="hero-reference"
            fill="currentColor"
            fontSize="10"
            fontFamily="monospace"
          >
            <text x="299" y="46">
              2400 mm
            </text>
            <text x="175" y="99">
              SWB-01
            </text>
            <text x="298" y="99">
              SWB-02
            </text>
            <text x="420" y="99">
              SWB-03
            </text>
            <text x="269" y="319">
              FRONT ELEVATION · E-10
            </text>
          </g>
          <g className="hero-markup">
            <rect
              x="405"
              y="245"
              width="63"
              height="42"
              rx="4"
              fill="currentColor"
              fillOpacity=".08"
              stroke="currentColor"
              strokeDasharray="4 3"
            />
            <circle cx="469" cy="246" r="13" fill="currentColor" />
            <text
              x="469"
              y="250"
              textAnchor="middle"
              fill="#19131f"
              fontSize="11"
              fontWeight="600"
            >
              07
            </text>
            <path
              d="M482 246Q535 235 550 190"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
            />
          </g>
        </svg>
        <div className="hero-annotation">
          <span>
            Comment 07 <span>Priya M. · p. 2</span>
          </span>
          <p>Confirm the cable entry location on drawing E-10.</p>
        </div>
        <div className="hero-connector" aria-hidden="true">
          <i />
          <span />
          <i />
        </div>
      </div>
      <div className="hero-record">
        <div className="hero-record-status">
          <StageIcon size={16} />
          <span>{current.status}</span>
          <span>Rev B</span>
        </div>
        <div className="hero-record-body" key={stage}>
          <strong>{current.title}</strong>
          <p>{current.detail}</p>
          <div className="hero-record-relation">
            <span>Comment 07</span>
            <ArrowRight size={13} />
            <span>
              {stage === 0
                ? "Original PDF"
                : stage === 1
                  ? "Source · page 2"
                  : stage === 2
                    ? "Drawing · E-10"
                    : "Recorded response"}
            </span>
          </div>
        </div>
      </div>
      <div className="hero-playback">
        <div role="group" aria-label="Example stages">
          {stages.map((item, index) => (
            <button
              key={item.name}
              aria-pressed={stage === index}
              onClick={() => {
                setStage(index);
                setPaused(true);
              }}
            >
              <span>0{index + 1}</span>
              {item.name}
            </button>
          ))}
        </div>
        <button
          className="hero-pause"
          aria-label={paused || reduced ? "Play example" : "Pause example"}
          disabled={reduced}
          onClick={() => setPaused(!paused)}
        >
          {paused || reduced ? <Play size={14} /> : <Pause size={14} />}
        </button>
      </div>
    </div>
  );
}
