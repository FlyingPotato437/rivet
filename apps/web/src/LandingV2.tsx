import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { ArrowDown, ArrowUpRight, ShieldCheck } from "@phosphor-icons/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import "@fontsource-variable/geist/wght.css";
import "@fontsource-variable/geist-mono/wght.css";
import { Mark } from "./ui";
import { RivetAssembly } from "./RivetAssembly";
import { RivetField } from "./LandingMotion";
import {
  WorkflowPreview,
  EngineerReview,
  SavingsEstimate,
  SecurityVisual,
} from "./HomepageWorkflow";
import "./landing-v2.css";
import "./homepage-workflow.css";

gsap.registerPlugin(ScrollTrigger);

function usePageMotion(
  root: RefObject<HTMLDivElement | null>,
  setStep: (step: number) => void,
) {
  useLayoutEffect(() => {
    if (!root.current) return;
    const mm = gsap.matchMedia();
    const ctx = gsap.context(() => {
      ScrollTrigger.create({
        start: 24,
        onUpdate: (self) =>
          root.current?.classList.toggle("is-scrolled", self.scroll() > 24),
      });
      // Discrete chapter changes only. Scroll never causes per-frame React renders.
      mm.add("(min-width: 801px)", () => {
        root
          .current!.querySelectorAll<HTMLElement>("[data-chapter]")
          .forEach((chapter, index) => {
            ScrollTrigger.create({
              trigger: chapter,
              start: "top 58%",
              end: "bottom 58%",
              onEnter: () => setStep(index),
              onEnterBack: () => setStep(index),
            });
          });
      });
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        gsap.from(".rvs-hero-copy > *", {
          y: 20,
          opacity: 0,
          duration: 0.85,
          stagger: 0.1,
          ease: "power3.out",
          clearProps: "transform,opacity",
        });
        gsap.utils.toArray<HTMLElement>("[data-reveal]").forEach((element) => {
          gsap.from(element, {
            y: 28,
            opacity: 0,
            duration: 0.8,
            ease: "power3.out",
            scrollTrigger: { trigger: element, start: "top 93%", once: true },
            clearProps: "transform,opacity",
          });
        });
        gsap.fromTo(
          ".rvs-reading-line",
          { scaleX: 0 },
          {
            scaleX: 1,
            ease: "none",
            scrollTrigger: {
              trigger: root.current,
              start: "top top",
              end: "bottom bottom",
              scrub: 0.3,
            },
          },
        );
        gsap.from(".rvs-footer-word", {
          yPercent: 24,
          opacity: 0.3,
          ease: "none",
          scrollTrigger: {
            trigger: ".rvs-footer",
            start: "top bottom",
            end: "bottom bottom",
            scrub: 0.8,
          },
        });
      });
    }, root);
    let live = true;
    void document.fonts.ready.then(() => {
      if (!live) return;
      ScrollTrigger.refresh();
      // Links from the booking page arrive before React mounts their anchor.
      const anchor = document.getElementById(location.hash.slice(1));
      if (anchor && root.current?.contains(anchor)) {
        anchor.scrollIntoView({ behavior: "instant", block: "start" });
      }
    });
    return () => {
      live = false;
      mm.revert();
      ctx.revert();
    };
  }, [root, setStep]);
}

const STEPS = [
  [
    "Read",
    "Find the request.",
    "Scan the markup. Connect each comment to its drawing location.",
  ],
  [
    "Revise",
    "Make the candidate.",
    "Use your rules to edit supported CAD geometry, ratings and BOM entries.",
  ],
  [
    "Re-check",
    "Read it back.",
    "Check the saved files. Bring unresolved engineering decisions to you.",
  ],
  [
    "Return",
    "Ready in Outlook.",
    "A cited reply and candidate files. Your engineer reviews; your team sends.",
  ],
] as const;

function Brand() {
  return (
    <a className="rvs-brand" href="/" aria-label="Rivet home">
      <Mark />
      <span>
        rivet<i>.</i>
      </span>
    </a>
  );
}

export function LandingV2() {
  const root = useRef<HTMLDivElement>(null);
  const [step, setStep] = useState(0);
  usePageMotion(root, setStep);
  return (
    <div className="rv-site" ref={root}>
      <a className="rvs-skip" href="#main">
        Skip to content
      </a>
      <header className="rvs-nav">
        <Brand />
        <nav aria-label="Sections">
          <a href="#how">How it works</a>
          <a href="#control">Engineer review</a>
          <a href="#proof">Time saved</a>
          <a href="#security">Security</a>
        </nav>
        <a className="rvs-btn is-solid is-small" href="/pilot">
          Request a pilot <ArrowUpRight size={15} />
        </a>
        <div className="rvs-reading-line" aria-hidden="true" />
      </header>
      <main id="main">
        <section className="rvs-hero">
          <div className="rvs-hero-light" aria-hidden="true" />
          <div className="rvs-wrap rvs-hero-grid">
            <div className="rvs-hero-copy">
              <p className="rvs-eyebrow">
                <i /> Built for electrical manufacturers
              </p>
              <h1>
                Customer markups in.<span>Revised drawings out.</span>
              </h1>
              <p className="rvs-lede">
                CAD changes, checked files, and an Outlook reply.
                <br className="rvs-desktop-break" /> Built around your workflow.
                Approved by your engineers.
              </p>
              <div className="rvs-actions">
                <a className="rvs-btn is-solid" href="/pilot">
                  Request a pilot <ArrowUpRight size={17} />
                </a>
                <a className="rvs-text-link" href="#how">
                  See how it works <ArrowDown size={16} />
                </a>
              </div>
            </div>
            <div className="rvs-hero-field">
              <RivetField />
            </div>
          </div>
          <div className="rvs-wrap rvs-hero-bottom">
            <p>Fits the tools you already use.</p>
            <ul aria-label="Supported tools and formats">
              <li>Outlook</li>
              <li>PDF markups</li>
              <li>DXF drawings</li>
              <li>Excel BOMs</li>
            </ul>
            <a href="#how" aria-label="Explore the workflow">
              <ArrowDown size={18} />
            </a>
          </div>
        </section>

        <section className="rvs-how rvs-wrap" id="how">
          <header id="workflow" className="rvs-section-heading" data-reveal>
            <p className="rvs-label">How it works</p>
            <h2>
              One revision.
              <br />
              <span>One connected workflow.</span>
            </h2>
          </header>
          <div className="rvs-story">
            <div className="rvs-chapters">
              {STEPS.map(([label, title, description], index) => (
                <article
                  className={`rvs-chapter${step === index ? " is-active" : ""}`}
                  data-chapter
                  key={label}
                >
                  <p className="rvs-label">
                    <span>0{index + 1}</span>
                    {label}
                  </p>
                  <h3>{title}</h3>
                  <p>{description}</p>
                </article>
              ))}
            </div>
            <div className="rvs-story-visual">
              <div className="rvs-story-sticky">
                <WorkflowPreview step={step} />
                <div
                  className="rvs-story-controls"
                  aria-label="Drawing illustration steps"
                >
                  {STEPS.map(([label], index) => (
                    <button
                      key={label}
                      onClick={() => setStep(index)}
                      aria-pressed={step === index}
                    >
                      <span>0{index + 1}</span>
                      {label}
                    </button>
                  ))}
                </div>
                <p className="rvs-example-note">
                  Interactive illustration · fictional files. Supported edits
                  depend on your CAD format and manufacturer rules.
                </p>
              </div>
            </div>
          </div>
        </section>

        <section
          className="rvs-connection rvs-wrap"
          aria-label="Connected order record"
        >
          <div data-reveal>
            <p className="rvs-label">One connected order</p>
            <h2>
              Every file.
              <br />
              <span>Working together.</span>
            </h2>
            <p className="rvs-connection-caption">
              Drawings · BOM · Customer history
            </p>
          </div>
          <div className="rvs-connection-assembly">
            <RivetAssembly compact />
          </div>
        </section>

        <section className="rvs-control" id="control">
          <div className="rvs-wrap rvs-control-grid">
            <div data-reveal>
              <EngineerReview />
            </div>
            <div className="rvs-control-copy" data-reveal>
              <p className="rvs-label">Engineer review</p>
              <h2>
                See the change.
                <br />
                <span>Keep the judgment.</span>
              </h2>
              <p>
                Compare the candidate with the original. See what passed the
                file checks—and what still needs your decision.
              </p>
              <div className="rvs-outlook-note">
                <ShieldCheck size={19} />
                <span>
                  Your engineer reviews.
                  <br />
                  Your team sends from Outlook.
                </span>
              </div>
            </div>
          </div>
        </section>

        <section className="rvs-proof rvs-wrap" id="proof">
          <div data-reveal>
            <p className="rvs-label">From request to candidate</p>
            <h2>
              Less manual work.
              <br />
              <span>More engineering time.</span>
            </h2>
            <ol className="rvs-candidate-path">
              <li>
                <span>01</span>
                <div>
                  <strong>Ground the change</strong>
                  <p>Your comment + native drawing + manufacturer rules.</p>
                </div>
              </li>
              <li>
                <span>02</span>
                <div>
                  <strong>Build a candidate copy</strong>
                  <p>Apply supported geometry, rating and BOM edits.</p>
                </div>
              </li>
              <li>
                <span>03</span>
                <div>
                  <strong>Check before review</strong>
                  <p>
                    Re-read saved files. Surface conflicts and missing inputs.
                  </p>
                </div>
              </li>
            </ol>
          </div>
          <div data-reveal>
            <SavingsEstimate />
          </div>
        </section>

        <section className="rvs-security rvs-wrap" id="security">
          <div data-reveal>
            <p className="rvs-label">Security</p>
            <h2>
              Your files.
              <br />
              <span>Your control.</span>
            </h2>
            <SecurityVisual />
          </div>
          <div className="rvs-security-details" data-reveal>
            <p className="rvs-security-intro">
              Start with a redacted package. Review access and data handling
              with your IT team before connecting systems.
            </p>
            <details>
              <summary>
                Originals stay intact <span>+</span>
              </summary>
              <p>
                Edits go into copies, with source references and a record of the
                changes.
              </p>
            </details>
            <details>
              <summary>
                Access agreed before connection <span>+</span>
              </summary>
              <p>
                Connect only the agreed mailbox and SharePoint sites. Each
                customer has a separate workspace. Replies remain unsent drafts;
                engineers approve drawing changes and release.
              </p>
            </details>
            <details>
              <summary>
                AI processing and retention, explained <span>+</span>
              </summary>
              <p>
                Documents are processed through the OpenAI API, which does not
                train on API data by default. Provider retention terms apply.
                Before a pilot, agree an NDA, hosting, access, retention and
                deletion on request. No security certifications are claimed.
              </p>
            </details>
          </div>
        </section>

        <section className="rvs-cta">
          <div className="rvs-wrap" data-reveal>
            <p className="rvs-label">Start with one order</p>
            <h2>
              Your drawings.
              <br />
              <span>Let’s put Rivet to work.</span>
            </h2>
            <a className="rvs-btn is-solid" href="/pilot">
              Request a pilot <ArrowUpRight size={18} />
            </a>
          </div>
        </section>
      </main>
      <footer className="rvs-footer rvs-wrap">
        <div className="rvs-footer-top">
          <Brand />
          <p>Built around the way you manufacture.</p>
          <span>© {new Date().getFullYear()} Rivet</span>
        </div>
        <div className="rvs-footer-word" aria-hidden="true">
          rivet<span>.</span>
        </div>
      </footer>
    </div>
  );
}
