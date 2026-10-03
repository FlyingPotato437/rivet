import { useEffect, useRef } from "react";
import "./site-atmosphere.css";

/** One continuous, quiet field of light behind the product story. */
export function SiteAtmosphere() {
  const field = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const sync = () => {
      if (field.current) field.current.dataset.paused = String(document.hidden);
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);
  return (
    <div className="site-atmosphere" ref={field} aria-hidden="true">
      <div className="site-atmosphere-light site-atmosphere-light-a" />
      <div className="site-atmosphere-light site-atmosphere-light-b" />
      <svg
        className="site-atmosphere-contours"
        viewBox="0 0 1600 1000"
        preserveAspectRatio="xMidYMid slice"
      >
        <defs>
          <linearGradient
            id="rivet-atmosphere-edge"
            x1="0"
            y1="0"
            x2="1"
            y2="1"
          >
            <stop offset="0" stopColor="#b5a6d9" stopOpacity="0" />
            <stop offset=".35" stopColor="#b5a6d9" stopOpacity=".16" />
            <stop offset=".7" stopColor="#9b80cb" stopOpacity=".08" />
            <stop offset="1" stopColor="#9b80cb" stopOpacity="0" />
          </linearGradient>
        </defs>
        <g fill="none" stroke="url(#rivet-atmosphere-edge)" strokeWidth=".7">
          {[0, 1, 2, 3, 4, 5].map((line) => (
            <path
              key={line}
              d={`M ${-160 + line * 22} -100 C ${480 + line * 18} 90, ${420 + line * 28} 475, ${1030 + line * 30} 585 S ${1620 + line * 25} 850, 1810 1120`}
            />
          ))}
        </g>
      </svg>
      <div className="site-atmosphere-grain" />
    </div>
  );
}
