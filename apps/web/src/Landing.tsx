import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCircle,
  CaretDown,
  CaretRight,
  FileText,
  Files,
  GitDiff,
  LinkSimple,
  List,
  LockKey,
  Play,
  ShieldCheck,
  Sparkle,
  Stack,
  X,
} from "@phosphor-icons/react";
import { Mark } from "./ui";
import { usd } from "./api";
import sample from "./marketing-sample.json";
import "./landing.css";
import { RivetField, ScrollJourney, useLandingMotion } from "./LandingMotion";
import "./landing-motion.css";

// Design: variance 6 / motion 8 / density 3. Lumina-inspired aesthetic,
// native CSS, existing Geist + Phosphor system. Radii: buttons 7px, panels 14px.
// Preview data is a captured synthetic Rivet project, never customer data.
const clean = (text: string) => text.replace(/[–—]/g, "-");
const sourceFor = (tag: string) =>
  sample.sources.find((s) => s.text.startsWith(tag + ":"));
const steps = [
  {
    title: "Bring the project together",
    label: "Upload",
    icon: Files,
    copy: "Start with a schedule, specification, supplier offer, or existing quote. Keep the original document alongside your work.",
    detail: "PDF, XLSX, CSV, or pasted text",
    screen: "sources",
  },
  {
    title: "Build a quote you can inspect",
    label: "Draft",
    icon: Stack,
    copy: "Turn equipment into editable lines. Set quantities, choose catalog items, and see exactly where the numbers came from.",
    detail: "Editable quantities, costs, prices, and terms",
    screen: "quote",
  },
  {
    title: "Give every change a clear review",
    label: "Review",
    icon: GitDiff,
    copy: "Compare an addendum with the current quote. Review proposed edits and resolve exceptions before applying a new revision.",
    detail: "Before-and-after values with source evidence",
    screen: "changes",
  },
  {
    title: "Put your name on the right version",
    label: "Export",
    icon: ShieldCheck,
    copy: "Approve a specific revision, then produce a customer-ready PDF or spreadsheet. Keep your internal costs inside the workspace.",
    detail: "PDF and XLSX from the approved quote",
    screen: "export",
  },
] as const;
const questions = [
  [
    "What is Rivet?",
    "Rivet is a workspace for equipment quoting. It brings project documents, editable quote lines, source evidence, and revisions into one place so estimators can review the details before approving a quote.",
  ],
  [
    "What can I bring into a project?",
    "Upload text-based PDFs, CSV or XLSX spreadsheets, and text files, or paste a request. Spreadsheet columns are confirmed before import. Scans without readable text are flagged for transcription or review.",
  ],
  [
    "Can I edit a quote myself?",
    "Yes. Edit quantities, costs, selling prices, equipment details, and commercial terms directly. Rivet records revisions and asks for reasons on commercial edits so you can follow what changed.",
  ],
  [
    "Does the AI make decisions for me?",
    "The assistant can inspect sources, look up catalog candidates, calculate prices, and propose changes. It asks for input when evidence is missing. You review the proposal and approve the quote; the assistant cannot approve, export, or send it for you.",
  ],
  [
    "How does Rivet handle an addendum?",
    "Keep the new document with the project and review proposed changes against the current revision. Partial addenda preserve equipment that was not mentioned. Missing prices, offer limits, and conflicting delivery details remain visible for review.",
  ],
  [
    "Can I try Rivet now?",
    "Yes. Open the local workspace to explore three fictional sample projects or create your own. This prototype runs on this computer. Team accounts, shared hosting, and production access are not available yet.",
  ],
];

export function Landing() {
  const [menu, setMenu] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useLandingMotion(root);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  const goTo = () => setMenu(false);
  return (
    <div className="rivet-site" ref={root}>
      <a className="site-skip" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <span className="site-reading-line" aria-hidden="true" />
        <div className="site-nav site-wrap">
          <a
            href="#home"
            className="site-brand"
            aria-label="Rivet home"
            onClick={() => {
              setMenu(false);
              window.scrollTo({ top: 0, behavior: "instant" });
            }}
          >
            <Mark />
            <span>
              rivet<span className="site-brand-dot">.</span>
            </span>
          </a>
          <nav
            aria-label="Main navigation"
            className={menu ? "site-links is-open" : "site-links"}
          >
            <a href="#product" onClick={goTo}>
              Product
            </a>
            <a href="#workflow" onClick={goTo}>
              How it works
            </a>
            <a href="#intelligence" onClick={goTo}>
              Intelligence
            </a>
            <a href="#faq" onClick={goTo}>
              FAQs
            </a>
          </nav>
          <a className="site-nav-cta" href="#projects">
            Open workspace <ArrowUpRight size={15} />
          </a>
          <button
            className="site-menu"
            aria-label={menu ? "Close menu" : "Open menu"}
            aria-expanded={menu}
            onClick={() => setMenu(!menu)}
          >
            {menu ? <X size={23} /> : <List size={23} />}
          </button>
        </div>
      </header>
      <main id="main">
        <section className="site-hero" aria-labelledby="hero-heading">
          <div className="site-hero-stage">
            <RivetField />
            <div className="site-hero-copy site-wrap">
              <a className="site-intro" href="#product">
                <span>Meet your new quoting workspace</span>
                <ArrowRight size={14} />
              </a>
              <h1 id="hero-heading">
                Every quote.
                <br />
                <span>Connected.</span>
              </h1>
              <p>
                Turn scattered project documents into clear, confident quotes.
                <br className="site-desktop-break" /> From the first line item
                to the final revision.
              </p>
              <div className="site-actions">
                <a className="site-button site-button-primary" href="#projects">
                  Start quoting <ArrowRight size={17} />
                </a>
                <a
                  className="site-button site-button-secondary"
                  href="#workflow"
                >
                  <Play size={14} weight="fill" /> See how it works
                </a>
              </div>
            </div>
          </div>
          <div className="site-product-reveal site-wrap">
            <div className="site-product-heading">
              <h2>Clarity in every line.</h2>
              <p>
                A workspace for the quote, the evidence, and everything that
                changes.
              </p>
            </div>
            <div className="site-hero-product">
              <div className="site-product-halo" aria-hidden="true" />
              <ProductPreview />
            </div>
            <div className="site-preview-caption">
              <span>
                <span className="site-live-dot" /> Interactive product preview
              </span>
              <span>
                Fictional sample project. Select a line to explore its source.
              </span>
            </div>
          </div>
        </section>
        <section
          className="site-formats site-wrap"
          aria-label="Supported input formats"
        >
          <p>
            All the pieces of a quote.
            <br />
            <strong>Finally, in one place.</strong>
          </p>
          <div>
            <FileText size={24} />
            <span>Specifications</span>
          </div>
          <div>
            <Stack size={24} />
            <span>Equipment schedules</span>
          </div>
          <div>
            <Files size={24} />
            <span>Supplier offers</span>
          </div>
          <div>
            <GitDiff size={24} />
            <span>Addenda</span>
          </div>
        </section>
        <section
          className="site-product site-wrap site-section"
          id="product"
          aria-labelledby="product-heading"
        >
          <div className="site-section-heading site-reveal">
            <span className="site-eyebrow">BUILT AROUND THE DETAILS</span>
            <h2 id="product-heading">
              The whole picture.
              <br />
              <span>Down to the line item.</span>
            </h2>
            <p>
              A quote is more than a spreadsheet. Keep the evidence, decisions,
              and changes connected to the work.
            </p>
          </div>
          <div className="site-feature-grid">
            <article className="site-feature site-evidence-feature site-reveal">
              <div className="site-feature-copy">
                <LinkSimple size={24} />
                <h3>Every number has a source.</h3>
                <p>
                  Follow a quantity or price back to the original document. Keep
                  the context one click away.
                </p>
              </div>
              <div className="site-source-visual">
                <div className="site-source-label">
                  <FileText size={17} /> Equipment schedule <span>Line 2</span>
                </div>
                <p>{clean(sourceFor("PDU-01")?.text ?? "")}</p>
                <div className="site-evidence-connection">
                  <LinkSimple size={15} />
                  <span>Connected to PDU-01</span>
                  <CheckCircle size={16} />
                </div>
              </div>
              <a className="site-text-link" href="#workflow">
                Explore the workflow <ArrowUpRight size={16} />
              </a>
            </article>
            <article className="site-feature site-revision-feature site-reveal">
              <div className="site-feature-copy">
                <GitDiff size={24} />
                <h3>Revisions without the guesswork.</h3>
                <p>
                  See what changed, why it changed, and what still needs your
                  attention.
                </p>
              </div>
              <div className="site-diff-visual">
                <div>
                  <FileText size={16} />
                  <span>Addendum 02</span>
                  <span className="site-small-status">Needs review</span>
                </div>
                <p>
                  PDU-01 <span>Quantity</span>
                </p>
                <div className="site-diff-numbers">
                  <span>
                    <small>CURRENT</small>8
                  </span>
                  <ArrowRight size={24} />
                  <span>
                    <small>PROPOSED</small>10
                  </span>
                </div>
                <div className="site-diff-note">
                  <ShieldCheck size={17} /> Existing supplier offer covers 8
                  units.
                </div>
              </div>
            </article>
            <article className="site-feature site-pricing-feature site-reveal">
              <div className="site-feature-copy">
                <Stack size={24} />
                <h3>Your numbers. Your control.</h3>
                <p>
                  Keep costs, selling prices, and margin explicit. Edit the
                  quote directly, with a history of every change.
                </p>
              </div>
              <dl className="site-price-visual">
                <div>
                  <dt>Supplier cost / unit</dt>
                  <dd>
                    $13,500<span>.00</span>
                  </dd>
                </div>
                <div>
                  <dt>Selling price / unit</dt>
                  <dd>
                    $18,000<span>.00</span>
                  </dd>
                </div>
              </dl>
              <span className="site-example-caption">
                PDU-01 from the synthetic sample project
              </span>
            </article>
            <article className="site-feature site-approval-feature site-reveal">
              <div className="site-feature-copy">
                <ShieldCheck size={24} />
                <h3>The final say stays with you.</h3>
                <p>
                  Review the details, approve a revision, and export the
                  customer quote. Internal costs stay in your workspace.
                </p>
              </div>
              <div className="site-approval-visual">
                <span>
                  <Check size={17} /> Review equipment
                </span>
                <span>
                  <Check size={17} /> Confirm terms
                </span>
                <strong>
                  <LockKey size={17} /> Approve this revision
                </strong>
                <div>
                  <span>PDF</span>
                  <span>XLSX</span>
                  <ArrowUpRight size={18} />
                </div>
              </div>
            </article>
          </div>
        </section>
        <ScrollJourney
          steps={steps}
          renderVisual={(step) => <WorkflowVisual step={step} />}
        />
        <section
          className="site-intelligence site-wrap site-section site-reveal"
          id="intelligence"
          aria-labelledby="intelligence-heading"
        >
          <div className="site-ai-visual">
            <div className="site-ai-symbol">
              <Mark />
            </div>
            <div className="site-ai-question">
              What changed in Addendum 02?
              <Sparkle size={18} />
            </div>
            <div className="site-ai-answer">
              <span>
                <Mark small /> RIVET
              </span>
              <p>{sample.proposals[0].summary}</p>
              <div>
                <FileText size={14} /> Addendum 02{" "}
                <span>2 source references</span>
              </div>
              <strong>
                <ShieldCheck size={16} /> Ready for your review
              </strong>
            </div>
            <span className="site-example-caption">
              Example drawn from the sample proposal
            </span>
          </div>
          <div className="site-ai-copy">
            <span className="site-eyebrow">INTELLIGENCE WITH CONTEXT</span>
            <h2>
              A second set of eyes.
              <br />
              <span>On every detail.</span>
            </h2>
            <p>
              Ask Rivet to inspect your sources, find catalog candidates, or
              work through a revision. Review the reasoning through its evidence
              and proposed edits.
            </p>
            <ul>
              <li>
                <CheckCircle size={18} /> Works from your project documents
              </li>
              <li>
                <CheckCircle size={18} /> Calls out missing information
              </li>
              <li>
                <CheckCircle size={18} /> Proposes changes for you to review
              </li>
            </ul>
            <a className="site-text-link" href="#guide">
              Explore what Rivet can do <ArrowUpRight size={16} />
            </a>
          </div>
        </section>
        <section
          className="site-faq site-wrap site-section"
          id="faq"
          aria-labelledby="faq-heading"
        >
          <div className="site-faq-heading site-reveal">
            <h2 id="faq-heading">
              A few things
              <br />
              <span>you might be asking.</span>
            </h2>
            <p>Get to know the workspace.</p>
          </div>
          <div className="site-faq-list">
            {questions.map(([q, a]) => (
              <details key={q}>
                <summary>
                  {q}
                  <span>
                    <CaretDown size={18} />
                  </span>
                </summary>
                <p>{a}</p>
              </details>
            ))}
          </div>
        </section>
        <section className="site-final site-wrap site-reveal">
          <div className="site-final-mark">
            <Mark />
          </div>
          <h2>
            Bring your next
            <br />
            <span>quote together.</span>
          </h2>
          <p>Start with the details. Keep everything connected.</p>
          <a className="site-button site-button-primary" href="#projects">
            Open Rivet <ArrowRight size={17} />
          </a>
          <span className="site-final-caption">
            Explore the local workspace with sample projects.
          </span>
        </section>
      </main>
      <footer className="site-footer site-wrap">
        <div className="site-footer-top">
          <div>
            <a
              className="site-brand"
              href="#home"
              onClick={() => window.scrollTo({ top: 0, behavior: "instant" })}
            >
              <Mark />
              <span>rivet.</span>
            </a>
            <p>Every quote, connected.</p>
          </div>
          <div>
            <span>PRODUCT</span>
            <a href="#product">Overview</a>
            <a href="#workflow">How it works</a>
            <a href="#intelligence">Intelligence</a>
          </div>
          <div>
            <span>EXPLORE</span>
            <a href="#projects">Workspace</a>
            <a href="#guide">Getting started</a>
            <a href="#faq">FAQs</a>
          </div>
        </div>
        <div className="site-footer-word" aria-hidden="true">
          rivet.
        </div>
        <div className="site-footer-bottom">
          <span>© {new Date().getFullYear()} Rivet</span>
          <span>Built for the details that matter.</span>
          <a
            href="#home"
            onClick={() => window.scrollTo({ top: 0, behavior: "instant" })}
          >
            Back to top <ArrowUpRight size={14} />
          </a>
        </div>
      </footer>
    </div>
  );
}

function ProductPreview() {
  const [view, setView] = useState("quote");
  const [selected, setSelected] = useState(sample.quote.lines[0]);
  const source = sourceFor(selected.tag);
  return (
    <div
      className="site-preview"
      aria-label="Interactive synthetic sample quote"
    >
      <aside className="site-preview-sidebar">
        <div className="site-preview-brand">
          <Mark small />
          <strong>rivet.</strong>
        </div>
        <span className="site-preview-team">
          R{" "}
          <span>
            Rivet workspace<small>Equipment quoting</small>
          </span>
        </span>
        <span className="site-preview-nav-label">WORKSPACE</span>
        <div className="site-preview-nav-item active">
          <Stack size={15} /> Projects <span>3</span>
        </div>
        <div className="site-preview-nav-item">
          <Files size={15} /> Equipment catalog
        </div>
        <div className="site-preview-nav-item">
          <GitDiff size={15} /> Activity
        </div>
        <span className="site-preview-nav-label">YOUR PROJECT</span>
        <p>Westfield Data Center</p>
        <div className="site-preview-sidebar-bottom">
          <LockKey size={15} />
          <span>
            Read-only preview<small>Synthetic sample data</small>
          </span>
        </div>
      </aside>
      <div className="site-preview-main">
        <div className="site-preview-topbar">
          <span>
            Projects <CaretRight size={12} /> Westfield Data Center
          </span>
          <span>
            <CheckCircle size={13} /> Saved locally
          </span>
        </div>
        <div className="site-preview-content">
          <div className="site-preview-title">
            <div>
              <span>ATLAS ELECTRICAL GROUP</span>
              <h3>{sample.project.title}</h3>
            </div>
            <a href="#projects">
              Open workspace <ArrowUpRight size={14} />
            </a>
          </div>
          <div className="site-preview-tabs">
            {["quote", "sources", "changes"].map((v) => (
              <button
                aria-pressed={view === v}
                onClick={() => setView(v)}
                key={v}
              >
                {v[0].toUpperCase() + v.slice(1)}{" "}
                <span>
                  {v === "quote"
                    ? sample.quote.lines.length
                    : v === "sources"
                      ? sample.documents.length
                      : 1}
                </span>
              </button>
            ))}
            <span>REV 03</span>
          </div>
          {view === "quote" ? (
            <>
              <div className="site-preview-metrics">
                <div>
                  <span>QUOTE TOTAL</span>
                  <strong>
                    {usd(sample.quote.total)}
                    <small>.00</small>
                  </strong>
                </div>
                <div>
                  <span>EQUIPMENT</span>
                  <strong>
                    06 <small>lines</small>
                  </strong>
                </div>
                <div>
                  <span>REVIEWED</span>
                  <strong>
                    04 <small>/ 06</small>
                  </strong>
                </div>
              </div>
              <button
                className="site-preview-notice"
                onClick={() => setView("changes")}
              >
                <GitDiff size={16} /> Addendum 02 has changes to review{" "}
                <ArrowRight size={14} />
              </button>
              <div className="site-preview-grid">
                <div className="site-preview-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Equipment</th>
                        <th>Qty</th>
                        <th>Unit price</th>
                        <th>Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sample.quote.lines.slice(0, 5).map((line) => (
                        <tr
                          className={
                            selected.id === line.id ? "is-selected" : ""
                          }
                          key={line.id}
                        >
                          <td>
                            <button
                              onClick={() => setSelected(line)}
                              aria-pressed={selected.id === line.id}
                            >
                              <span>
                                {line.tag}
                                <LinkSimple size={11} />
                              </span>
                              <strong>{line.description}</strong>
                            </button>
                          </td>
                          <td>{Number(line.quantity)}</td>
                          <td>{usd(line.price)}</td>
                          <td>{usd(line.extended)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <aside className="site-preview-evidence" aria-live="polite">
                  <span>
                    <LinkSimple size={13} /> SOURCE EVIDENCE
                  </span>
                  <h4>{selected.tag}</h4>
                  <p>{selected.description}</p>
                  <div>
                    <span>
                      <FileText size={14} /> Equipment schedule
                    </span>
                    <blockquote>
                      {clean(source?.text ?? "Source requires review.")}
                    </blockquote>
                    <small>{source?.location.label}</small>
                  </div>
                  <p className="site-evidence-end">
                    <ShieldCheck size={14} /> Kept with the quote
                  </p>
                </aside>
              </div>
            </>
          ) : view === "sources" ? (
            <div className="site-preview-docs">
              <h4>Your source documents.</h4>
              <p>
                Originals and their evidence stay connected to this project.
              </p>
              {sample.documents.map((doc) => (
                <div key={doc.id}>
                  <FileText size={24} />
                  <span>
                    {clean(doc.name)}
                    <small>Original retained · Text extracted</small>
                  </span>
                  <CheckCircle size={17} />
                </div>
              ))}
            </div>
          ) : (
            <div className="site-preview-changes">
              <span className="site-small-status">Pending review</span>
              <h4>{sample.proposals[0].title}</h4>
              <p>{sample.proposals[0].summary}</p>
              <div>
                <span>PDU-01 quantity</span>
                <s>8</s>
                <ArrowRight size={18} />
                <strong>10</strong>
              </div>
              <div>
                <span>TX-01 delivery</span>
                <strong>Supplier confirmation needed</strong>
              </div>
              <a href="#projects" className="site-text-link">
                Review in workspace <ArrowUpRight size={15} />
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function WorkflowVisual({ step }: { step: number }) {
  if (step === 0)
    return (
      <div className="site-workflow-art site-files-art">
        <div className="site-file-stack">
          <Files size={42} />
          <span>Project documents</span>
          <div>
            {[
              "Equipment schedule.xlsx",
              "Project specification.pdf",
              "Supplier offer.csv",
            ].map((name) => (
              <span key={name}>
                <FileText size={18} />
                {name}
                <Check size={15} />
              </span>
            ))}
          </div>
        </div>
      </div>
    );
  if (step === 1)
    return (
      <div className="site-workflow-art site-line-art">
        <span className="site-art-label">SAMPLE QUOTE LINE</span>
        <h4>PDU-01</h4>
        <p>Power distribution unit</p>
        <dl>
          <div>
            <dt>Quantity</dt>
            <dd>8 each</dd>
          </div>
          <div>
            <dt>Unit price</dt>
            <dd>$18,000.00</dd>
          </div>
          <div>
            <dt>Line total</dt>
            <dd>$144,000.00</dd>
          </div>
        </dl>
        <span className="site-art-source">
          <LinkSimple size={15} /> Equipment schedule, line 2
        </span>
      </div>
    );
  if (step === 2)
    return (
      <div className="site-workflow-art site-review-art">
        <GitDiff size={34} />
        <h4>
          Change the quote.
          <br />
          Keep the history.
        </h4>
        <div>
          <span>Current revision</span>
          <strong>03</strong>
          <ArrowRight size={22} />
          <span>Next revision</span>
          <strong>04</strong>
        </div>
        <p>Every applied change creates a new revision.</p>
      </div>
    );
  return (
    <div className="site-workflow-art site-export-art">
      <div>
        <Mark small />
        <strong>QUOTATION</strong>
        <span>Customer copy</span>
      </div>
      <h4>Westfield Data Center</h4>
      <p>Equipment, quantities, prices, and terms.</p>
      <div className="site-export-formats">
        <FileText size={28} />
        <span>PDF</span>
        <Files size={28} />
        <span>XLSX</span>
      </div>
      <span className="site-art-source">
        <LockKey size={15} /> Export follows review and approval
      </span>
    </div>
  );
}
