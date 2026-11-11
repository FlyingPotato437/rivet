import { useId, useState } from "react";
import {
  ArrowRight,
  Check,
  FilePdf,
  FileXls,
  Cube,
  EnvelopeSimple,
  LockSimple,
  ShieldCheck,
} from "@phosphor-icons/react";

const RATINGS = [
  ["RATED POWER", "2500 kVA", "3000 kVA"],
  ["PRIMARY", "12.47 kV", "13.8 kV"],
  ["SECONDARY", "600Y/346 V", "600Y/346 V"],
  ["PHASE / FREQUENCY", "3 PH / 60 Hz", "3 PH / 60 Hz"],
  ["PRIMARY CURRENT", "115.7 A", "125.5 A"],
  ["SECONDARY CURRENT", "2405.6 A", "2886.8 A"],
];

/** Fictional, dimensioned illustration. No customer drawing or engineering approval implied. */
export function EngineeringSheet({
  revised = false,
  scanning = false,
  checking = false,
}: {
  revised?: boolean;
  scanning?: boolean;
  checking?: boolean;
}) {
  const id = useId().replace(/:/g, "");
  return (
    <svg
      className="rvs-engineering-sheet"
      viewBox="0 0 800 500"
      role="img"
      aria-label={`Fictional transformer drawing, revision ${revised ? "B candidate" : "A"}: ${revised ? "3000 kVA, 13.8 kV" : "2500 kVA, 12.47 kV"}. Customer requests 3000 kVA and 13.8 kV; engineering approval remains required.`}
    >
      <defs>
        <pattern
          id={`${id}-grid`}
          width="20"
          height="20"
          patternUnits="userSpaceOnUse"
        >
          <path
            d="M20 0H0V20"
            fill="none"
            stroke="#718287"
            strokeOpacity=".075"
            strokeWidth=".5"
          />
        </pattern>
        <marker
          id={`${id}-arrow`}
          markerWidth="5"
          markerHeight="5"
          refX="2.5"
          refY="2.5"
          orient="auto-start-reverse"
        >
          <path
            d="M5 0L0 2.5L5 5"
            fill="none"
            stroke="#697575"
            strokeWidth=".8"
          />
        </marker>
        <linearGradient id={`${id}-scan`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#8b79ad" stopOpacity="0" />
          <stop offset="1" stopColor="#8b79ad" stopOpacity=".2" />
        </linearGradient>
        <clipPath id={`${id}-clip`}>
          <rect x="20" y="20" width="760" height="410" />
        </clipPath>
      </defs>
      <rect width="800" height="500" fill="#eceee9" />
      <rect width="800" height="500" fill={`url(#${id}-grid)`} />
      <g fill="none" stroke="#768180" strokeWidth=".65">
        <rect x="20" y="20" width="760" height="460" />
        {[210, 400, 590].map((x) => (
          <path key={x} d={`M${x} 20v-6M${x} 480v6`} />
        ))}
        <path d="M20 160h-6M780 160h6M20 310h-6M780 310h6M20 430H780" />
      </g>
      <g
        fontFamily="Geist Mono Variable, monospace"
        fill="#737f7e"
        fontSize="7"
        textAnchor="middle"
      >
        <text x="115" y="13">
          1
        </text>
        <text x="305" y="13">
          2
        </text>
        <text x="495" y="13">
          3
        </text>
        <text x="685" y="13">
          4
        </text>
        <text x="10" y="95">
          A
        </text>
        <text x="10" y="240">
          B
        </text>
        <text x="10" y="380">
          C
        </text>
      </g>
      <g fill="#384746" fontFamily="Geist Mono Variable, monospace">
        <text x="43" y="46" fontSize="10">
          T1 · TRANSFORMER GENERAL ARRANGEMENT
        </text>
        <text x="43" y="61" fontSize="7" fill="#7a8583">
          ILLUSTRATIVE GEOMETRY · DIMENSIONS IN mm · NOT FOR CONSTRUCTION
        </text>
      </g>
      <g fill="none" stroke="#344745" strokeWidth="1.15" strokeLinejoin="round">
        {/* Tank, cover, lifting lugs, radiators, feet and terminal bushings. */}
        <rect x="107" y="172" width="220" height="138" />
        <path d="M99 167h236v5H99zM107 310h220v8H107zM116 318v9h30v-9M289 318v9h30v-9M107 178h220M113 184v120h208V184z" />
        <path d="M119 167v-12h14v12M302 167v-12h14v12" />
        {[151, 217, 283].map((x, i) => (
          <g key={x}>
            <path
              d={`M${x - 7} 167v-9h14v9M${x - 4} 158v-39h8v39M${x} 119v-12`}
            />
            {[128, 136, 144, 152].map((y) => (
              <path key={y} d={`M${x - 8} ${y}h16`} />
            ))}
            <text
              x={x}
              y="99"
              fill="#344745"
              stroke="none"
              textAnchor="middle"
              fontSize="8"
            >
              H{i + 1}
            </text>
          </g>
        ))}
        <rect x="58" y="196" width="43" height="98" />
        <rect x="333" y="196" width="43" height="98" />
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <path key={i} d={`M${62 + i * 6} 200v90M${337 + i * 6} 200v90`} />
        ))}
        <path d="M101 208h6M101 281h6M327 208h6M327 281h6M50 331h335" />
        <rect x="267" y="214" width="46" height="70" />
        <rect x="272" y="219" width="36" height="59" />
        <circle cx="302" cy="248" r="1.5" />
        <circle cx="147" cy="205" r="10" />
        <path d="M147 205l4-5" />
        <rect x="166" y="227" width="56" height="29" />
        <path d="M173 234h42M173 240h42M173 246h30M122 304v-12h14v12" />
        <path
          d="M217 82v262"
          strokeDasharray="9 3 2 3"
          stroke="#83918e"
          strokeWidth=".65"
        />
      </g>
      <g stroke="#7a8886" strokeWidth=".65" fill="none">
        <path d="M58 299v64M376 299v64M394 167h22M394 327h22" />
        <path
          d="M61 355h312M409 170v154"
          markerStart={`url(#${id}-arrow)`}
          markerEnd={`url(#${id}-arrow)`}
        />
      </g>
      <g
        fill="#546460"
        fontSize="8"
        fontFamily="Geist Mono Variable, monospace"
      >
        <rect x="191" y="348" width="53" height="13" fill="#eceee9" />
        <text x="217" y="357" textAnchor="middle">
          2 400
        </text>
        <text
          x="420"
          y="257"
          transform="rotate(-90 420 257)"
          textAnchor="middle"
        >
          1 850
        </text>
        <text x="217" y="383" textAnchor="middle" fontSize="10">
          FRONT ELEVATION
        </text>
        <text x="217" y="398" textAnchor="middle" fontSize="7">
          SCHEMATIC · NOT TO SCALE
        </text>
      </g>
      {/* Nameplate values come from the same illustrative change as the one-line. */}
      <g fontFamily="Geist Mono Variable, monospace">
        <text x="464" y="84" fontSize="9" fill="#344745">
          NAMEPLATE SCHEDULE · T1
        </text>
        <rect
          x="458"
          y="95"
          width="292"
          height="168"
          fill="#f4f5f0"
          stroke="#71807c"
          strokeWidth=".7"
        />
        {RATINGS.map(([label, before, after], i) => (
          <g key={label}>
            {revised && before !== after && (
              <rect
                className="rvs-cell-revised"
                x="628"
                y={96 + i * 28}
                width="121"
                height="27"
                fill="#dfe5e1"
              />
            )}
            <path
              d={`M458 ${123 + i * 28}h292`}
              stroke="#a6afaa"
              strokeWidth=".5"
            />
            <text x="469" y={113 + i * 28} fill="#65716c" fontSize="8">
              {label}
            </text>
            <text x="640" y={113 + i * 28} fill="#263b32" fontSize="10">
              {revised ? after : before}
            </text>
            {checking && before !== after && (
              <path
                d={`M733 ${106 + i * 28}l3 3 5-6`}
                stroke="#416954"
                strokeWidth="1.4"
                fill="none"
              />
            )}
          </g>
        ))}
        <path d="M627 95v168" stroke="#a6afaa" strokeWidth=".5" />
        <text x="464" y="290" fontSize="9" fill="#344745">
          SINGLE-LINE DETAIL
        </text>
        <g stroke="#344745" strokeWidth="1.1" fill="none">
          <path d="M490 307v15m0 34v31m0-49v-2" />
          <circle cx="490" cy="331" r="11" />
          <circle cx="490" cy="348" r="11" />
          <path d="M486 332l4-6 4 6zM485 348l5 3m0 0v5m0-5l5-3M483 387h14M486 390h8M489 393h2" />
        </g>
        <g fill="#4f605b" fontSize="9">
          <text x="517" y="316">
            {revised ? "13.8" : "12.47"} kV · Δ
          </text>
          <text x="517" y="342">
            T1 / {revised ? "3000" : "2500"} kVA
          </text>
          <text x="517" y="369">
            600Y/346 V · 3 PH
          </text>
        </g>
      </g>
      {!revised && (
        <g
          className="rvs-redlines"
          fill="none"
          stroke="#b44a45"
          strokeWidth="1.7"
          strokeLinecap="round"
        >
          <ellipse
            cx="684"
            cy="110"
            rx="56"
            ry="15"
            transform="rotate(-4 684 110)"
          />
          <ellipse
            cx="684"
            cy="138"
            rx="57"
            ry="15"
            transform="rotate(2 684 138)"
          />
          <path d="M606 58q68-4 81 35m-6-4 6 4 2-7M692 153q46 47 36 131l-4-5m4 5 5-5" />
          <g
            fill="#b44a45"
            stroke="none"
            fontFamily="Georgia, serif"
            fontStyle="italic"
            fontSize="13"
          >
            <text x="476" y="52" transform="rotate(-2 476 52)">
              3000 kVA per load study
            </text>
            <text x="609" y="305" transform="rotate(-3 609 305)">
              13.8 kV primary
            </text>
          </g>
        </g>
      )}
      {scanning && (
        <g clipPath={`url(#${id}-clip)`}>
          <g className="rvs-sheet-sweep">
            <rect
              x="20"
              y="-70"
              width="760"
              height="70"
              fill={`url(#${id}-scan)`}
            />
            <path d="M20 0H780" stroke="#8b79ad" strokeWidth="1.2" />
          </g>
        </g>
      )}
      <g fontFamily="Geist Mono Variable, monospace" fill="#4f605b">
        <path
          d="M444 430v50M630 430v50M718 430v50M444 454h336"
          stroke="#768180"
          strokeWidth=".65"
        />
        <text x="34" y="449" fontSize="9">
          RIVET / FICTIONAL ENGINEERING EXAMPLE
        </text>
        <text x="34" y="466" fontSize="7">
          DESIGN, EQUIPMENT SUITABILITY AND CLEARANCES REQUIRE ENGINEER REVIEW
        </text>
        <text x="454" y="444" fontSize="7">
          DRAWING NUMBER
        </text>
        <text x="454" y="471" fontSize="12">
          RVT-T1-GA-001
        </text>
        <text x="640" y="444" fontSize="7">
          REVISION
        </text>
        <text x="640" y="471" fontSize="11">
          {revised ? "B / DRAFT" : "A"}
        </text>
        <text x="728" y="444" fontSize="7">
          SHEET
        </text>
        <text x="728" y="471" fontSize="11">
          01 / 03
        </text>
      </g>
    </svg>
  );
}

const STATES = [
  {
    title: "Reading the returned drawing",
    agent: "Markup reader",
    line: "2 requests located on sheet GA-001",
    detail: "Ratings linked to their exact source cells.",
  },
  {
    title: "Building a coordinated candidate",
    agent: "Drawing + BOM tools",
    line: "Drawing, one-line and BOM updated together",
    detail: "Supported edits applied to a working copy.",
  },
  {
    title: "Re-opening the saved files",
    agent: "Independent file checker",
    line: "4 changed values re-read from the saved drawing",
    detail: "Electrical design and clearances still need your engineer.",
  },
  {
    title: "Prepared for your Outlook thread",
    agent: "Reply preparation",
    line: "A cited reply, with the candidate files attached",
    detail: "Nothing sent. Nothing released.",
  },
];

export function WorkflowPreview({ step }: { step: number }) {
  const state = STATES[step];
  return (
    <figure
      className={`rvs-workflow-preview is-step-${step}`}
      aria-label="Interactive fictional workflow illustration"
    >
      <div className="rvs-preview-top">
        <span>
          <i />
          {state.title}
        </span>
        <span>DEMO / T1</span>
      </div>
      <div className="rvs-preview-stage" key={step}>
        {step === 3 ? (
          <div className="rvs-mail-preview">
            <div className="rvs-mail-heading">
              <EnvelopeSimple size={20} />
              <span>Outlook / Reply draft</span>
              <span className="rvs-draft-badge">Unsent</span>
            </div>
            <div className="rvs-mail-subject">
              <span>RE: T1 — returned approval drawings</span>
              <small>To: Customer project team · illustrative</small>
            </div>
            <p>Hi Alex,</p>
            <p>
              We’ve prepared a revised drawing and BOM for your review. The
              candidate reflects the requested{" "}
              <strong>3000 kVA / 13.8 kV</strong> ratings.
            </p>
            <span className="rvs-citation-tag">↗ Customer markup · GA-001</span>
            <p>
              Engineering approval of the revised design remains outstanding.
              These files are draft copies.
            </p>
            <div className="rvs-attachments">
              <span>
                <Cube size={18} />
                T1_Rev-B.dxf<small>Candidate drawing</small>
              </span>
              <span>
                <FileXls size={18} />
                T1_BOM.xlsx<small>Updated schedule</small>
              </span>
              <span>
                <FilePdf size={18} />
                Change_log.pdf<small>Sources + checks</small>
              </span>
            </div>
          </div>
        ) : (
          <EngineeringSheet
            revised={step > 0}
            scanning={step === 0}
            checking={step === 2}
          />
        )}
        {step === 1 && (
          <div className="rvs-bom-strip">
            <FileXls size={15} />
            <span>BOM · T1</span>
            <s>2500 kVA / 12.47 kV</s>
            <ArrowRight size={13} />
            <strong>3000 kVA / 13.8 kV</strong>
          </div>
        )}
      </div>
      <figcaption className="rvs-agent-result" aria-live="polite">
        <span className="rvs-agent-icon">
          {step === 2 ? (
            <Check size={17} />
          ) : (
            <span className="rvs-agent-orbit" />
          )}
        </span>
        <div>
          <small>{state.agent}</small>
          <strong>{state.line}</strong>
          <p>{state.detail}</p>
        </div>
      </figcaption>
    </figure>
  );
}

export function EngineerReview() {
  const [proposed, setProposed] = useState(true);
  return (
    <figure
      className="rvs-engineer-panel"
      aria-label="Illustrative engineer review of a cabinet relocation"
    >
      <div className="rvs-preview-top">
        <span>Change 03 / Cabinet relocation</span>
        <span>EXAMPLE</span>
      </div>
      <div className="rvs-review-switch" aria-label="Compare drawing versions">
        <button aria-pressed={!proposed} onClick={() => setProposed(false)}>
          Original
        </button>
        <button aria-pressed={proposed} onClick={() => setProposed(true)}>
          Candidate copy
        </button>
      </div>
      <svg
        viewBox="0 0 600 290"
        role="img"
        aria-label={
          proposed
            ? "Candidate cabinet moved to opposite face. Mounting and clearances await engineer confirmation."
            : "Original cabinet position beside transformer."
        }
      >
        <defs>
          <pattern
            id="rvs-review-grid"
            width="20"
            height="20"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M20 0H0V20"
              fill="none"
              stroke="#ced4e3"
              strokeOpacity=".07"
            />
          </pattern>
        </defs>
        <rect width="600" height="290" fill="url(#rvs-review-grid)" />
        <g stroke="#84908f" fill="none" strokeWidth="1.1">
          <rect x="160" y="64" width="280" height="156" />
          <rect x="174" y="77" width="252" height="130" />
          <path
            d="M184 64V45h48v19M368 64V45h48v19M184 220v19h48v-19M368 220v19h48v-19M300 33v224"
            strokeDasharray="5 4"
          />
          {[226, 300, 374].map((x) => (
            <circle key={x} cx={x} cy="116" r="13" />
          ))}
          <rect x="274" y="160" width="52" height="25" />
        </g>
        {proposed && (
          <g fill="none" stroke="#87908e" strokeDasharray="4 4">
            <rect x="442" y="134" width="62" height="66" />
            <path d="M460 123Q300 -18 117 123" stroke="#bcb0d5" />
            <path d="M117 114v9h9" stroke="#bcb0d5" />
          </g>
        )}
        <g
          className="rvs-cabinet-position"
          transform={`translate(${proposed ? 96 : 442},134)`}
          stroke={proposed ? "#d1c2ec" : "#bbc5c0"}
          fill={proposed ? "#b7a1de18" : "#b9c4bb10"}
          strokeWidth="1.4"
        >
          <rect width="62" height="66" />
          <rect x="5" y="5" width="52" height="56" />
          <circle cx="48" cy="32" r="2" />
          <text
            x="31"
            y="85"
            fill={proposed ? "#d1c2ec" : "#bbc5c0"}
            stroke="none"
            textAnchor="middle"
            fontSize="10"
          >
            CAB-01
          </text>
        </g>
        <g
          fill="#9fa9aa"
          fontFamily="Geist Mono Variable, monospace"
          fontSize="9"
        >
          <text x="24" y="27">
            PLAN / PLACEMENT CANDIDATE
          </text>
          <text x="300" y="276" textAnchor="middle">
            ILLUSTRATIVE GEOMETRY · NOT AN APPROVED LOCATION
          </text>
        </g>
      </svg>
      <div className="rvs-review-evidence">
        <p>
          <Check size={15} />
          <span>Candidate saved and geometry re-read</span>
        </p>
        <p>
          <Cube size={15} />
          <span>Mounting + clearance confirmation required</span>
        </p>
      </div>
      <figcaption>
        <ShieldCheck size={17} />
        <span>Engineer decides what is accepted.</span>
        <span>Not released</span>
      </figcaption>
    </figure>
  );
}

export function SavingsEstimate() {
  const [rounds, setRounds] = useState(8);
  const [manual, setManual] = useState(90);
  const [assisted, setAssisted] = useState(30);
  const hours = Math.max(0, (rounds * (manual - assisted)) / 60);
  return (
    <div className="rvs-estimator">
      <p className="rvs-label">Your time, modelled</p>
      <div className="rvs-estimate-total">
        <output aria-live="polite" aria-label="Estimated hours saved per month">
          {hours.toLocaleString("en-US", { maximumFractionDigits: 1 })}
          <span>h</span>
        </output>
        <span>
          potentially saved
          <br />
          per month
        </span>
      </div>
      <div className="rvs-estimate-inputs">
        <label>
          <span>Revision rounds / month</span>
          <input
            type="number"
            min="0"
            max="1000"
            value={rounds}
            onChange={(e) =>
              setRounds(
                Math.max(0, Math.min(1000, Number(e.target.value) || 0)),
              )
            }
          />
        </label>
        <label>
          <span>Minutes / round today</span>
          <input
            type="number"
            min="0"
            max="10000"
            value={manual}
            onChange={(e) =>
              setManual(
                Math.max(0, Math.min(10000, Number(e.target.value) || 0)),
              )
            }
          />
        </label>
        <label>
          <span>Minutes with Rivet, including review</span>
          <input
            type="number"
            min="0"
            max="10000"
            value={assisted}
            onChange={(e) =>
              setAssisted(
                Math.max(0, Math.min(10000, Number(e.target.value) || 0)),
              )
            }
          />
        </label>
      </div>
      <p className="rvs-estimate-note">
        Illustrative estimate, not a measured result. Edit the assumptions
        above; validate them on your pilot.
      </p>
    </div>
  );
}

export function SecurityVisual() {
  return (
    <figure
      className="rvs-security-map"
      aria-label="Data flow: selected files enter a customer workspace, with API processing disclosed and engineer-controlled outputs."
    >
      <div className="rvs-security-boundary">
        <div className="rvs-security-boundary-label">
          <LockSimple size={14} />
          Customer workspace
        </div>
        <div className="rvs-security-nodes">
          <span>
            <FilePdf size={23} />
            Selected files<small>Access agreed</small>
          </span>
          <ArrowRight size={18} />
          <span>
            <ShieldCheck size={25} />
            Working copies<small>Originals preserved</small>
          </span>
        </div>
        <div className="rvs-security-exit">
          <Check size={14} />
          Engineer review before sending or release
        </div>
      </div>
      <figcaption>
        <span className="rvs-provider-line" />
        <span>
          AI processing via OpenAI API
          <small>
            No training on API data by default. Provider retention terms apply.
          </small>
        </span>
      </figcaption>
    </figure>
  );
}
