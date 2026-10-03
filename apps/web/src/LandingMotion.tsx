import { useLayoutEffect, useRef, type RefObject } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

/** All scroll effects are scoped, reversible, and removed when the app opens. */
export function useLandingMotion(root: RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    if (!root.current) return;
    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      const ctx = gsap.context(() => {
        gsap.from(".site-hero-copy > *", {
          y: 28,
          opacity: 0,
          duration: 1.1,
          stagger: 0.11,
          ease: "power3.out",
          clearProps: "transform,opacity",
        });
        gsap.to(".site-hero-copy", {
          y: -75,
          opacity: 0.2,
          ease: "none",
          scrollTrigger: {
            trigger: ".site-hero-stage",
            start: "20% top",
            end: "bottom top",
            scrub: 1,
          },
        });
        gsap.fromTo(
          ".site-hero-product",
          { y: 90, rotateX: 12, scale: 0.93 },
          {
            y: 0,
            rotateX: 0,
            scale: 1,
            ease: "none",
            scrollTrigger: {
              trigger: ".site-product-reveal",
              start: "top 95%",
              end: "35% 50%",
              scrub: 1.1,
            },
          },
        );
        gsap.to(".site-reading-line", {
          scaleX: 1,
          ease: "none",
          scrollTrigger: {
            trigger: root.current,
            start: "top top",
            end: "bottom bottom",
            scrub: 0.2,
          },
        });
        gsap.utils
          .toArray<HTMLElement>(
            ".site-section-heading h2, .rivet-assistant-heading h2, .site-faq-heading h2, .site-final h2",
          )
          .forEach((title) => {
            gsap.from(title, {
              y: 32,
              opacity: 0.15,
              duration: 1,
              ease: "power3.out",
              scrollTrigger: {
                trigger: title,
                start: "top 90%",
                toggleActions: "play none none reverse",
              },
            });
          });
        gsap.from(".site-footer-word", {
          yPercent: 40,
          opacity: 0.15,
          ease: "none",
          scrollTrigger: {
            trigger: ".site-footer",
            start: "top bottom",
            end: "bottom bottom",
            scrub: 1,
          },
        });
      }, root);
      return () => ctx.revert();
    });
    let live = true;
    let resizeTimer: number | undefined;
    let refreshFrame = 0;
    const scheduleRefresh = () => {
      window.clearTimeout(resizeTimer);
      cancelAnimationFrame(refreshFrame);
      resizeTimer = window.setTimeout(() => {
        resizeTimer = undefined;
        refreshFrame = requestAnimationFrame(() => {
          refreshFrame = 0;
          if (live) ScrollTrigger.refresh();
        });
      }, 100);
    };
    const sizes = new WeakMap<Element, { width: number; height: number }>();
    const contentObserver = new ResizeObserver((entries) => {
      let changed = false;
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        const previous = sizes.get(entry.target);
        if (
          previous &&
          (Math.abs(previous.width - width) > 0.5 ||
            Math.abs(previous.height - height) > 0.5)
        ) {
          changed = true;
        }
        sizes.set(entry.target, { width, height });
      }
      if (changed) scheduleRefresh();
    });
    // Content boxes exclude transforms and pin spacers, preventing refresh loops.
    root.current
      .querySelectorAll(
        ".site-preview, #product, #workflow, #intelligence, .site-faq-list",
      )
      .forEach((element) => contentObserver.observe(element));
    void document.fonts.ready.then(() => {
      if (live) ScrollTrigger.refresh();
    });
    return () => {
      live = false;
      contentObserver.disconnect();
      window.clearTimeout(resizeTimer);
      cancelAnimationFrame(refreshFrame);
      mm.revert();
    };
  }, [root]);
}

/** The existing Rivet mark is rendered as a dimensional field of light.
 * Canvas remains decorative; all content and controls are ordinary HTML.
 * No React updates occur per frame. Off-screen and reduced-motion frames pause.
 */
export function RivetField() {
  const canvas = useRef<HTMLCanvasElement>(null);
  useLayoutEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const context = el.getContext("2d", { alpha: true });
    if (!context) return;
    const c = context;
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    let width = 0,
      height = 0,
      visible = true,
      last = 0,
      pointerX = 0,
      pointerY = 0;
    const motion = { progress: 0 };
    const assemblyStart = performance.now();
    const mark = new Path2D(
      "M5 5h14.5v6H11v6h8.5v10H5V5Zm14.5 6H27v6h-7.5V11Zm0 6H25l4 10h-7l-2.5-10Z",
    );
    const points: { x: number; y: number; z: number; edge: boolean }[] = [];
    for (let z = -3; z <= 3; z += 1) {
      for (let y = 4; y <= 28; y += 0.55)
        for (let x = 4; x <= 29; x += 0.55) {
          if (!c.isPointInPath(mark, x, y)) continue;
          const edge =
            !c.isPointInPath(mark, x + 0.7, y) ||
            !c.isPointInPath(mark, x - 0.7, y) ||
            !c.isPointInPath(mark, x, y + 0.7) ||
            !c.isPointInPath(mark, x, y - 0.7);
          if (Math.abs(z) === 3 || edge)
            points.push({
              x: (x - 17) / 13,
              y: (y - 16) / 13,
              z: z * 0.1,
              edge,
            });
        }
    }
    function draw(time = 0) {
      if (!visible || document.hidden || width === 0) return;
      c.clearRect(0, 0, width, height);
      const mobile = width < 700;
      const px = width * (mobile ? 0.6 : 0.78),
        py = height * (mobile ? 0.75 : 0.49);
      const scale = Math.min(width * (mobile ? 0.49 : 0.25), height * 0.45);
      const slow = media.matches ? 0 : time;
      const assembled = media.matches
        ? 1
        : Math.min(1, (performance.now() - assemblyStart) / 1700);
      const spread = Math.pow(1 - assembled, 3);
      const progress = media.matches ? 0 : motion.progress;
      const ry =
        -0.4 + Math.sin(slow * 0.2) * 0.12 + pointerX * 0.16 + progress * 0.7;
      const rx = -0.12 + Math.cos(slow * 0.15) * 0.05 + pointerY * 0.1;
      const rz = -0.1 + progress * 0.12;
      const cy = Math.cos(ry),
        sy = Math.sin(ry),
        cx = Math.cos(rx),
        sx = Math.sin(rx),
        cz = Math.cos(rz),
        sz = Math.sin(rz);
      const glow = c.createRadialGradient(px, py, 0, px, py, scale * 1.55);
      glow.addColorStop(0, "rgba(152,94,229,.14)");
      glow.addColorStop(0.55, "rgba(93,51,152,.06)");
      glow.addColorStop(1, "rgba(30,16,54,0)");
      c.fillStyle = glow;
      c.fillRect(0, 0, width, height);
      const projected = points
        .map((p) => {
          const x = p.x * cy + p.z * sy,
            z = -p.x * sy + p.z * cy,
            y = p.y * cx - z * sx,
            zz = p.y * sx + z * cx;
          const depth = 3.7 / (3.7 + zz);
          return {
            x:
              px +
              (x * cz - y * sz) * scale * depth +
              Math.sin(p.x * 29 + p.y * 17) * scale * spread,
            y:
              py +
              (x * sz + y * cz) * scale * depth +
              Math.cos(p.y * 31 + p.x * 13) * scale * spread,
            z: zz,
            depth,
            edge: p.edge,
            face: p.z,
          };
        })
        .sort((a, b) => b.z - a.z);
      for (const p of projected) {
        const light = 0.28 + (1 - (p.z + 1) / 2) * 0.65;
        const shimmer =
          0.9 + Math.sin(p.x * 0.027 + p.y * 0.019 - slow * 0.75) * 0.1;
        const alpha = Math.min(0.95, light * shimmer * (p.edge ? 1.2 : 1));
        c.fillStyle = `rgba(${p.edge ? 224 : 179},${p.edge ? 199 : 140},${p.edge ? 250 : 228},${alpha})`;
        const size = Math.max(0.75, scale * 0.0044 * p.depth);
        c.fillRect(p.x, p.y, size * (p.edge ? 1.4 : 1), size * 0.8);
      }
      // Traces approach the mark, expressing many document details joining one quote.
      for (let row = 0; row < 22; row++) {
        const y = py - scale * 0.9 + row * scale * 0.084;
        const start = width * (mobile ? 0.1 : 0.38),
          end = px - scale * 0.7;
        if (end <= start) continue;
        const fade = c.createLinearGradient(start, 0, end, 0);
        fade.addColorStop(0, "rgba(140,102,190,0)");
        fade.addColorStop(
          1,
          `rgba(178,135,226,${row % 3 === 0 ? 0.14 : 0.055})`,
        );
        c.strokeStyle = fade;
        c.lineWidth = 0.6;
        c.beginPath();
        c.moveTo(start, y);
        c.lineTo(end, y);
        c.stroke();
        if (!media.matches && row % 3 === 0) {
          const travel = (slow * 0.085 + row * 0.13) % 1;
          const x = start + (end - start) * travel;
          c.fillStyle = `rgba(199,164,244,${travel * 0.45})`;
          c.fillRect(x, y - 0.8, 12, 1.2);
        }
      }
    }
    const resize = () => {
      const r = el.getBoundingClientRect();
      width = r.width;
      height = r.height;
      const dpr = Math.min(devicePixelRatio, 1.5);
      el.width = Math.round(width * dpr);
      el.height = Math.round(height * dpr);
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      draw();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    const visibility = new IntersectionObserver((entries) => {
      visible = entries[0].isIntersecting;
      if (visible) draw();
    });
    visibility.observe(el);
    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      pointerX = (e.clientX - r.left) / r.width - 0.5;
      pointerY = (e.clientY - r.top) / r.height - 0.5;
    };
    const stage = el.parentElement!;
    stage.addEventListener("pointermove", move, { passive: true });
    const tick = (time: number) => {
      if (media.matches || time - last < 0.033) return;
      last = time;
      draw(time);
    };
    gsap.ticker.add(tick);
    const changed = () => draw();
    media.addEventListener("change", changed);
    const trigger = ScrollTrigger.create({
      trigger: stage,
      start: "top top",
      end: "bottom top",
      onUpdate: (s) => {
        motion.progress = s.progress;
        if (media.matches) draw();
      },
    });
    resize();
    return () => {
      observer.disconnect();
      visibility.disconnect();
      stage.removeEventListener("pointermove", move);
      media.removeEventListener("change", changed);
      gsap.ticker.remove(tick);
      trigger.kill();
    };
  }, []);
  return (
    <canvas ref={canvas} className="site-rivet-field" aria-hidden="true" />
  );
}
