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
import { HeroReview } from "./HeroReview";
import { PilotWaitlist } from "./PilotWaitlist";
import { SiteAtmosphere } from "./SiteAtmosphere";
import "./site-refinement.css";

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
    "Rivet matches explicit equipment tags and drawing identifiers within the order. Unambiguous matches can be linked automatically, with their evidence and an undo action. Conflicting matches appear in Overview; missing references get a clarification draft. The original source is always retained.",
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
      <div className="rivet-site" id="home" ref={root}>
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
              <a href="#preview" onClick={goTo}>
                Product
              </a>
              <a href="#workflow" onClick={goTo}>
                How it works
              </a>
              <a href="#pilot" onClick={goTo}>
                Pilot
              </a>
              <a href="#faq" onClick={goTo}>
                FAQs
              </a>
              <a className="site-mobile-signin" href="#orders" onClick={goTo}>
                Sign in
              </a>
            </nav>
            <a className="site-signin" href="#orders">
              Sign in
            </a>
            <a className="site-nav-cta" href="#pilot">
              Request a pilot <ArrowUpRight size={15} />
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
              <div className="site-hero-grain" aria-hidden="true" />
              <div className="site-hero-layout site-wrap">
                <div className="site-hero-copy">
                  <a className="site-intro" href="#preview">
                    <span>For custom equipment manufacturers</span>
                    <ArrowRight size={14} />
                  </a>
                  <h1 id="hero-heading">
                    Every comment.
                    <br />
                    Every revision.
                    <br />
                    <span>One order record.</span>
                  </h1>
                  <p>
                    Rivet turns returned submittals, marked-up drawings, and
                    emails into a connected record of what was asked, how it was
                    answered, and what changed.
                  </p>
                  <div className="site-actions">
                    <a
                      className="site-button site-button-primary"
                      href="#pilot"
                    >
                      Request a pilot <ArrowRight size={17} />
                    </a>
                    <a
                      className="site-button site-button-secondary"
                      href="#preview"
                    >
                      Explore the product <ArrowRight size={17} />
                    </a>
                  </div>
                  <span className="site-hero-note">
                    Built for the manufacturer’s side of the submittal process.
                  </span>
                </div>
                <div className="site-hero-mark" aria-hidden="true">
                  <RivetField />
                </div>
              </div>
            </div>
          </section>
          <section
            className="site-inputs site-wrap"
            aria-label="Supported sources"
          >
            <span>Start with what your customers send.</span>
            <div>
              <span>Marked-up PDFs</span>
              <i />
              <span>Submittal revisions</span>
              <i />
              <span>Saved emails</span>
              <i />
              <span>Comment trackers</span>
            </div>
          </section>
          <section
            className="site-product-reveal site-wrap"
            aria-labelledby="preview-heading"
            id="preview"
          >
            <div className="site-product-heading">
              <h2 id="preview-heading">
                Review the work.
                <br />
                <span>Keep the source in reach.</span>
              </h2>
              <p>
                Follow a returned drawing from the original markup to a captured
                comment, a confirmed reference, and a recorded response.
              </p>
            </div>
            <div className="site-hero-product site-product-demo">
              <div className="site-product-halo" aria-hidden="true" />
              <HeroReview />
            </div>
            <div className="site-preview-caption">
              <span>Explore the four steps</span>
              <span>
                Select a step or let the example play. Illustrative documents;
                technical decisions stay with your team.
              </span>
            </div>
          </section>
          <RivetAssembly />
          <section
            className="site-principles site-wrap"
            aria-label="How Rivet handles your work"
          >
            <div>
              <span>01 / Evidence</span>
              <h3>Open the original.</h3>
              <p>
                Go from a comment to its source page. The original wording and
                document stay attached.
              </p>
            </div>
            <div>
              <span>02 / Review</span>
              <h3>Keep decisions with people.</h3>
              <p>
                Unclear references are flagged. Your team records technical
                responses and approvals.
              </p>
            </div>
            <div>
              <span>03 / Communication</span>
              <h3>Prepare the handoff.</h3>
              <p>
                Share a reviewed record and prepare change notices for the
                customer and production team.
              </p>
            </div>
          </section>
          <PilotWaitlist />
          <section
            className="site-faq site-wrap site-section"
            id="faq"
            aria-labelledby="faq-heading"
          >
            <div className="site-faq-heading site-reveal">
              <h2 id="faq-heading">
                A few practical
                <br />
                <span>questions.</span>
              </h2>
              <p>What to expect from Rivet and the pilot.</p>
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
              <p>Comment and revision records for equipment manufacturers.</p>
            </div>
            <div>
              <span>Product</span>
              <a href="#preview">Overview</a>
              <a href="#workflow">How it works</a>
              <a href="#pilot">Pilot waitlist</a>
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
            <span>For custom equipment manufacturers.</span>
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
