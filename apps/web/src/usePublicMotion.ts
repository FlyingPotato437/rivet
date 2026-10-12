import { useLayoutEffect, type RefObject } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

/** Short, scoped transitions that leave the page fully readable without motion. */
export function usePublicMotion(root: RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    const container = root.current;
    if (!container) return;

    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      const context = gsap.context(() => {
        const hero = container.querySelectorAll(".public-hero-copy > *");
        if (hero.length) {
          gsap.from(hero, {
            y: 16,
            opacity: 0,
            duration: 0.65,
            stagger: 0.08,
            ease: "power2.out",
            clearProps: "transform,opacity",
          });
        }

        container
          .querySelectorAll<HTMLElement>("[data-public-reveal]")
          .forEach((element) => {
            gsap.from(element, {
              y: 18,
              opacity: 0,
              duration: 0.6,
              ease: "power2.out",
              clearProps: "transform,opacity",
              scrollTrigger: {
                trigger: element,
                start: "top 94%",
                once: true,
              },
            });
          });

        container
          .querySelectorAll<HTMLElement>(".public-process-track")
          .forEach((track) => {
            const lines = track.querySelectorAll(".public-process-line");
            if (lines.length) {
              gsap.fromTo(
                lines,
                { strokeDashoffset: 1000 },
                {
                  strokeDashoffset: 0,
                  ease: "none",
                  scrollTrigger: {
                    trigger: track,
                    start: "top 85%",
                    end: "bottom 45%",
                    scrub: 0.5,
                  },
                },
              );
            }

            const nodes = track.querySelectorAll(".public-process-node");
            if (nodes.length) {
              gsap.from(nodes, {
                y: 10,
                opacity: 0,
                duration: 0.5,
                stagger: 0.09,
                ease: "power2.out",
                clearProps: "transform,opacity",
                scrollTrigger: {
                  trigger: track,
                  start: "top 88%",
                  once: true,
                },
              });
            }
          });

        container
          .querySelectorAll<HTMLElement>(".public-digest")
          .forEach((digest) => {
            gsap.fromTo(
              digest,
              { y: 18 },
              {
                y: 0,
                ease: "none",
                scrollTrigger: {
                  trigger: digest,
                  start: "top 92%",
                  end: "top 48%",
                  scrub: 0.5,
                },
              },
            );
          });
      }, container);

      // matchMedia runs this cleanup when the preference changes, restoring
      // the normal document immediately rather than leaving hidden elements.
      return () => context.revert();
    });

    let mounted = true;
    void document.fonts.ready.then(() => {
      if (mounted) ScrollTrigger.refresh();
    });

    return () => {
      mounted = false;
      media.revert();
    };
  }, [root]);
}
