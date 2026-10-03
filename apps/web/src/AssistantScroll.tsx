import { useLayoutEffect, useRef, useState } from "react";
import { ArrowDown, ArrowRight } from "@phosphor-icons/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { drawQuoteReviewScene } from "./QuoteReviewScene";
import "./assistant-scroll.css";

gsap.registerPlugin(ScrollTrigger);

const checks = [
  {
    label: "Revisions",
    title: "Compare the revision documents.",
    answer:
      "A new drawing arrives while work is already underway. Keep the previous package and its comments available as you review the new revision.",
    source: "Illustrative package · Rev B → Rev C",
  },
  {
    label: "Comments",
    title: "Locate the original comment.",
    answer:
      "A reviewer requests a different rating. Retain the exact comment and confirm which drawing it refers to before recording the response.",
    source: "Original markup → reviewed drawing location",
  },
  {
    label: "Changes",
    title: "Record the requested change.",
    answer:
      "Keep the old and requested values, the person who asked, and the recorded approval together. Prepare a notice for everyone affected.",
    source: "Comment → change record → notice draft",
  },
];
const clamp = (value: number) => Math.max(0, Math.min(1, value));

/** A reversible scroll sequence. Only stage boundaries update React. */
export function AssistantScroll() {
  const [active, setActive] = useState(0);
  const root = useRef<HTMLElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const fill = useRef<HTMLSpanElement>(null);
  const choose = useRef<(index: number) => void>(() => {});

  useLayoutEffect(() => {
    const section = root.current,
      content = panel.current,
      element = canvas.current;
    const context = element?.getContext("2d", { alpha: true });
    if (!section || !content || !element || !context) return;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    let trigger: ScrollTrigger | undefined;
    let progress = 0,
      selected = -1,
      frame = 0,
      lastFrame = 0;
    let width = 0,
      height = 0,
      visible = false,
      disposed = false;
    let pinned = false,
      configKey = "",
      resizeFrame = 0;

    const draw = (now: number) => {
      frame = 0;
      if (disposed || !visible || document.hidden || !width) return;
      if (now - lastFrame < 1000 / 30) {
        schedule();
        return;
      }
      lastFrame = now;
      drawQuoteReviewScene(context, width, height, progress);
    };
    const schedule = () => {
      if (!frame && visible && !document.hidden && !disposed)
        frame = requestAnimationFrame(draw);
    };
    const update = (value: number) => {
      progress = clamp(value);
      if (fill.current) fill.current.style.transform = `scaleX(${progress})`;
      const index = Math.min(2, Math.floor(progress * 3));
      if (index !== selected) {
        selected = index;
        setActive(index);
      }
      schedule();
    };
    const configure = () => {
      resizeFrame = 0;
      if (disposed) return;
      const rect = element.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      const dpr = Math.min(devicePixelRatio || 1, 1.7);
      element.width = Math.round(width * dpr);
      element.height = Math.round(height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      const top = innerWidth <= 800 ? 82 : 100;
      const panelHeight = Math.ceil(content.getBoundingClientRect().height);
      const canPin = !reduced.matches && panelHeight <= innerHeight - top - 12;
      const travel = Math.max(innerHeight * 1.6, 1000);
      const key = `${canPin}:${panelHeight}:${innerWidth}:${innerHeight}:${reduced.matches}`;
      if (key !== configKey) {
        configKey = key;
        pinned = canPin;
        section.classList.toggle("is-scroll-story", pinned);
        section.style.setProperty("--story-panel-height", `${panelHeight}px`);
        section.style.setProperty("--story-travel", `${travel}px`);
        section.style.setProperty("--story-top", `${top}px`);
        trigger?.kill();
        trigger = undefined;
        if (!reduced.matches) {
          trigger = ScrollTrigger.create({
            trigger: section,
            start: pinned ? `top ${top}px` : "top 60%",
            end: pinned ? `+=${travel}` : "bottom 45%",
            invalidateOnRefresh: true,
            onUpdate: (self) => update(self.progress),
            onRefresh: (self) => update(self.progress),
          });
        }
      }
      schedule();
    };
    const resized = () => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(configure);
    };
    choose.current = (index) => {
      const target = (index + 0.5) / 3;
      if (pinned && trigger && !reduced.matches) {
        window.scrollTo({
          top: trigger.start + (trigger.end - trigger.start) * target,
          behavior: "smooth",
        });
      } else update(target);
    };
    const observer = new IntersectionObserver(
      ([entry]) => {
        visible = entry.isIntersecting;
        if (visible) schedule();
        else {
          cancelAnimationFrame(frame);
          frame = 0;
        }
      },
      { rootMargin: "40px" },
    );
    observer.observe(content);
    const resizeObserver = new ResizeObserver(resized);
    resizeObserver.observe(content);
    const visibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(frame);
        frame = 0;
      } else schedule();
    };
    window.addEventListener("resize", resized);
    document.addEventListener("visibilitychange", visibility);
    reduced.addEventListener("change", resized);
    void document.fonts.ready.then(() => {
      if (!disposed) resized();
    });
    configure();
    return () => {
      disposed = true;
      trigger?.kill();
      cancelAnimationFrame(frame);
      cancelAnimationFrame(resizeFrame);
      observer.disconnect();
      resizeObserver.disconnect();
      window.removeEventListener("resize", resized);
      document.removeEventListener("visibilitychange", visibility);
      reduced.removeEventListener("change", resized);
      choose.current = () => {};
      section.classList.remove("is-scroll-story");
    };
  }, []);

  const check = checks[active];
  return (
    <section
      className="assistant-story"
      id="intelligence"
      ref={root}
      aria-labelledby="assistant-story-heading"
    >
      <div className="assistant-story-panel site-wrap" ref={panel}>
        <div className="assistant-story-copy">
          <div className="assistant-story-eyebrow">
            <span>Your order, connected</span>
            <span aria-hidden="true">0{active + 1} / 03</span>
          </div>
          <h2 id="assistant-story-heading">
            Track the change.
            <br />
            <span>Keep the context.</span>
          </h2>
          <p className="assistant-story-intro">
            Connect the comment to the drawing, the response, and the approved
            record. Keep the people affected informed.
          </p>
          <div
            className="assistant-story-controls"
            role="group"
            aria-label="Explore the revision record"
          >
            {checks.map((item, index) => (
              <button
                key={item.label}
                type="button"
                aria-pressed={active === index}
                aria-controls="assistant-story-answer"
                onClick={() => choose.current(index)}
              >
                <span>{item.label}</span>
                <ArrowRight size={13} aria-hidden="true" />
              </button>
            ))}
          </div>
          <div
            className="assistant-story-answer"
            id="assistant-story-answer"
            aria-live="polite"
            aria-atomic="true"
          >
            <h3>{check.title}</h3>
            <p>{check.answer}</p>
            <span>{check.source}</span>
          </div>
          <p className="assistant-story-note">
            Illustrative equipment example · fictional project
          </p>
        </div>
        <div className="assistant-story-art">
          <canvas ref={canvas} aria-hidden="true" />
        </div>
        <div className="assistant-story-scroll" aria-hidden="true">
          <span>
            Scroll to explore <ArrowDown size={12} />
          </span>
          <div>
            <span ref={fill} />
          </div>
        </div>
      </div>
    </section>
  );
}
