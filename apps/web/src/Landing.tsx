import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  CaretDown,
  List,
  IconContext,
  X,
} from "@phosphor-icons/react";
import { Mark } from "./ui";
import "./landing.css";
import { RivetField, useLandingMotion } from "./LandingMotion";
import { RivetAssembly } from "./RivetAssembly";
import "./landing-motion.css";
import { SourceJunction } from "./SourceJunction";
import { AssistantScroll } from "./AssistantScroll";
import { SiteAtmosphere } from "./SiteAtmosphere";
import { SampleOrderPreview } from "./OrderPreview";

const questions = [
  [
    "What is Rivet?",
    "Rivet builds the comment and change record for custom equipment orders. The manufacturer, customer team, and production team can trace what was asked, how it was answered, and what was approved.",
  ],
  [
    "What documents can I bring?",
    "Upload marked-up PDFs, drawings, text files, spreadsheets, or saved EML emails with supported attachments. Originals stay attached to the order. Scans and graphical markups without text require manual review.",
  ],
  [
    "What happens when a comment is unclear?",
    "Rivet matches explicit equipment tags and drawing identifiers within the order. Unambiguous matches can be linked automatically, with their evidence and an undo action. Conflicting matches go to the Work queue; missing references get a clarification draft. The original source is always retained.",
  ],
  [
    "Does Rivet suggest engineering fixes?",
    "Rivet prepares documentation and coordination work: drawing links, clarification drafts, assignments, and status updates. Engineers decide the technical response, and people record approvals. A linked drawing or completed task is not proof of engineering compliance.",
  ],
  [
    "What happens when a new revision arrives?",
    "Previous comments and responses remain in the log. Compare extracted text between two documents, record confirmed changes, and connect each change to its comments and approval history.",
  ],
  [
    "Can the customer and factory see the same record?",
    "Approved versions have revocable read-only links. On a hosted workspace, recipients can open the reviewed record and its included sources. Excel and PDF exports also work through your existing channels. Links from a local demo remain local to that computer.",
  ],
  [
    "How do change notices work?",
    "Add recipients from the customer, manufacturer, and production teams. Changes prepare notices for review, and you can enable a weekly digest draft per order. Review before sending from your email app or through a configured verified sender. Drafts are never treated as sent messages.",
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
    <IconContext.Provider value={{ weight: "regular" }}>
      <div className="rivet-site" ref={root}>
        <SiteAtmosphere />
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
                Changes
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
              <div className="site-hero-grain" aria-hidden="true" />
              <div className="site-hero-copy site-wrap">
                <a className="site-intro" href="#product">
                  <span>Comment records for custom equipment</span>
                  <ArrowRight size={14} />
                </a>
                <h1 id="hero-heading">
                  Projects change.
                  <br />
                  <span>Keep the order in sync.</span>
                </h1>
                <p>
                  Rivet brings every review comment, response, and approved
                  change into one record, linked to the drawings. Keep your
                  customer and production teams working from the same
                  information.
                </p>
                <div className="site-actions">
                  <a
                    className="site-button site-button-primary"
                    href="#projects"
                  >
                    Open workspace <ArrowRight size={17} />
                  </a>
                  <a
                    className="site-button site-button-secondary"
                    href="#workflow"
                  >
                    See how it works <ArrowRight size={17} />
                  </a>
                </div>
              </div>
            </div>
          </section>
          <SourceJunction />
          <RivetAssembly />
          <section
            className="site-product-reveal site-wrap"
            aria-labelledby="preview-heading"
            id="preview"
          >
            <div className="site-product-heading">
              <h2 id="preview-heading">
                Every comment and change.
                <br />
                All within reach.
              </h2>
              <p>
                Review the comment log, follow each source, and record the
                response. Compare revisions without losing the conversation.
              </p>
            </div>
            <div className="site-hero-product">
              <div className="site-product-halo" aria-hidden="true" />
              <SampleOrderPreview />
            </div>
            <div className="site-preview-caption">
              <span>Interactive preview</span>
              <span>
                Follow one review from the original comment to a drawing link,
                response, and notice draft. Fictional example.
              </span>
            </div>
          </section>
          <AssistantScroll />
          <section
            className="site-faq site-wrap site-section"
            id="faq"
            aria-labelledby="faq-heading"
          >
            <div className="site-faq-heading site-reveal">
              <h2 id="faq-heading">
                Before you
                <br />
                <span>get started.</span>
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
              Keep your next
              <br />
              <span>order in sync.</span>
            </h2>
            <p>The comment, the response, and the approval — together.</p>
            <a className="site-button site-button-primary" href="#projects">
              Open Rivet <ArrowRight size={17} />
            </a>
            <span className="site-final-caption">
              Explore the local workspace with sample orders.
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
              <p>Every detail, in order.</p>
            </div>
            <div>
              <span>Product</span>
              <a href="#product">Overview</a>
              <a href="#workflow">How it works</a>
              <a href="#intelligence">Changes</a>
            </div>
            <div>
              <span>Explore</span>
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
            <span>Comment records for custom equipment.</span>
            <a
              href="#home"
              onClick={() => window.scrollTo({ top: 0, behavior: "instant" })}
            >
              Back to top <ArrowUpRight size={14} />
            </a>
          </div>
        </footer>
      </div>
    </IconContext.Provider>
  );
}
