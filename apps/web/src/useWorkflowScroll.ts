import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

/** Three readable stops in normal page scroll; no wheel interception or timer. */
export function useWorkflowScroll(
  view: RefObject<HTMLDivElement | null>,
  onStage: (stage: number) => void,
) {
  const callback = useRef(onStage);
  const trigger = useRef<ScrollTrigger | null>(null);
  const current = useRef(0);
  const [scrollDriven, setScrollDriven] = useState(false);

  useLayoutEffect(() => {
    callback.current = onStage;
  }, [onStage]);

  useLayoutEffect(() => {
    const element = view.current;
    if (!element) return;
    const media = gsap.matchMedia();
    // Keep the entire interactive example readable on short screens and phones.
    // Those layouts retain the same direct, keyboard-accessible stage controls.
    media.add(
      "(min-width: 801px) and (min-height: 700px) and (prefers-reduced-motion: no-preference)",
      () => {
        setScrollDriven(true);
        element.dataset.scrollDriven = "true";
        trigger.current = ScrollTrigger.create({
          trigger: element,
          pin: true,
          start: "top 24px",
          end: () => `+=${Math.max(1500, window.innerHeight * 2.1)}`,
          anticipatePin: 1,
          invalidateOnRefresh: true,
          onUpdate: ({ progress }) => {
            const stage = Math.min(2, Math.floor(progress * 3));
            element.style.setProperty(
              "--stage-progress",
              String(Math.min(1, progress * 3 - stage)),
            );
            if (stage !== current.current) {
              current.current = stage;
              callback.current(stage);
            }
          },
        });
        return () => {
          trigger.current?.kill();
          trigger.current = null;
          delete element.dataset.scrollDriven;
          element.style.removeProperty("--stage-progress");
          setScrollDriven(false);
        };
      },
    );
    return () => media.revert();
  }, [view]);

  const goToStep = useCallback((stage: number) => {
    current.current = stage;
    callback.current(stage);
    const active = trigger.current;
    if (active) {
      // Land inside the requested stop, away from a stage boundary.
      window.scrollTo({
        top: active.start + (active.end - active.start) * ((stage + 0.2) / 3),
        behavior: "instant",
      });
      ScrollTrigger.update();
    }
  }, []);

  return { scrollDriven, goToStep };
}
