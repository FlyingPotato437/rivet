import { useLayoutEffect, useRef, useState } from "react";
import "./source-junction.css";

type V3 = [number, number, number];
type Material = "steel" | "edge" | "cable" | "recess" | "light";
type Face = {
  points: V3[];
  normal: V3;
  material: Material;
  tone: number;
  channel: number;
};
type Controller = { select: (index: number) => void };
const formats = [
  {
    label: "PDF",
    name: "Specifications",
    copy: "Keep the original package and its drawing locations available.",
  },
  {
    label: "XLSX",
    name: "Comment trackers",
    copy: "Keep the reviewer, response, and status together.",
  },
  {
    label: "EML",
    name: "Customer emails",
    copy: "Retain the message and its attachments in the order record.",
  },
  {
    label: "TXT",
    name: "Review comments",
    copy: "Record the response and link the drawing it refers to.",
  },
] as const;
const clamp = (v: number, low = 0, high = 1) =>
  Math.max(low, Math.min(high, v));
const normalise = (v: V3): V3 => {
  const n = Math.hypot(...v) || 1;
  return [v[0] / n, v[1] / n, v[2] / n];
};
const cross = (a: V3, b: V3): V3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const subtract = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const curve = (points: V3[], t: number): V3 => {
  const u = 1 - t;
  return [0, 1, 2].map(
    (axis) =>
      u ** 3 * points[0][axis] +
      3 * u * u * t * points[1][axis] +
      3 * u * t * t * points[2][axis] +
      t ** 3 * points[3][axis],
  ) as V3;
};
const inputPaths: V3[][] = [-2.13, -0.71, 0.71, 2.13].map((start, index) => {
  const end = [-0.96, -0.32, 0.32, 0.96][index];
  return [
    [-5.25, start, 0.34],
    [-3.75, start, 0.34],
    [-3.28, end, 0.34],
    [-2.08, end, 0.34],
  ];
});
const outputPath: V3[] = [
  [2.18, 0, 0.34],
  [3.1, 0, 0.34],
  [3.68, 0.43, 0.34],
  [5.1, 0.43, 0.34],
];

function makeMesh(): Face[] {
  const faces: Face[] = [];
  const face = (points: V3[], material: Material, tone = 1, channel = -1) =>
    faces.push({
      points,
      normal: normalise(
        cross(subtract(points[1], points[0]), subtract(points[2], points[0])),
      ),
      material,
      tone,
      channel,
    });
  const join = (a: V3[], b: V3[], material: Material, tone = 1, channel = -1) =>
    a.forEach((point, i) => {
      const next = (i + 1) % a.length;
      face([point, a[next], b[next], b[i]], material, tone, channel);
    });
  const ringX = (
    x: number,
    y: number,
    z: number,
    radius: number,
    count = 16,
  ): V3[] =>
    Array.from({ length: count }, (_, i) => [
      x,
      y + Math.cos((i / count) * Math.PI * 2) * radius,
      z + Math.sin((i / count) * Math.PI * 2) * radius,
    ]);
  const ringZ = (
    x: number,
    y: number,
    z: number,
    radius: number,
    count = 16,
  ): V3[] =>
    Array.from({ length: count }, (_, i) => [
      x + Math.cos((i / count) * Math.PI * 2) * radius,
      y + Math.sin((i / count) * Math.PI * 2) * radius,
      z,
    ]);
  const collar = (
    x: number,
    y: number,
    radius: number,
    length: number,
    channel: number,
  ) => {
    const profiles = [
      [0, radius * 0.8],
      [0.055, radius],
      [0.115, radius],
      [0.13, radius * 0.91],
      [0.18, radius * 0.91],
      [0.2, radius],
      [length - 0.12, radius],
      [length - 0.09, radius * 0.89],
      [length, radius * 0.89],
    ];
    let previous: V3[] | undefined;
    profiles.forEach(([distance, r], i) => {
      const ring = ringX(x + distance, y, 0.34, r);
      if (previous)
        join(
          previous,
          ring,
          i === 4 ? "light" : i % 2 ? "edge" : "steel",
          i % 2 ? 0.9 : 0.72,
          channel,
        );
      previous = ring;
    });
    const outer = ringX(x, y, 0.34, radius * 0.8);
    const inner = ringX(x - 0.003, y, 0.34, radius * 0.43);
    outer.forEach((point, i) => {
      const n = (i + 1) % outer.length;
      face([point, inner[i], inner[n], outer[n]], "edge", 0.8);
    });
    face([...inner].reverse(), "recess", 0.9);
  };
  const tube = (path: V3[], radius: number, channel: number) => {
    let previous: V3[] | undefined;
    for (let i = 0; i <= 28; i++) {
      const t = i / 28,
        centre = curve(path, t);
      const tangent = normalise(
        subtract(
          curve(path, Math.min(1, t + 0.01)),
          curve(path, Math.max(0, t - 0.01)),
        ),
      );
      const side = normalise([-tangent[1], tangent[0], 0]);
      const up = normalise(cross(tangent, side));
      const ring: V3[] = Array.from({ length: 10 }, (_, j) => {
        const angle = (j / 10) * Math.PI * 2;
        return centre.map(
          (v, k) =>
            v + radius * (Math.cos(angle) * side[k] + Math.sin(angle) * up[k]),
        ) as V3;
      });
      if (previous)
        join(previous, ring, "cable", i % 3 === 0 ? 0.8 : 1, channel);
      previous = ring;
    }
  };
  inputPaths.forEach((path, index) => tube(path, 0.095, index));
  tube(outputPath, 0.18, -2);
  const outline = (x: number, y: number, cut: number, z: number): V3[] => [
    [-x + cut, -y, z],
    [x - cut, -y, z],
    [x, -y + cut, z],
    [x, y - cut, z],
    [x - cut, y, z],
    [-x + cut, y, z],
    [-x, y - cut, z],
    [-x, -y + cut, z],
  ];
  const base = outline(1.3, 1.36, 0.21, 0.06);
  const lower = outline(1.42, 1.47, 0.23, 0.18);
  const upper = outline(1.42, 1.47, 0.23, 0.61);
  const top = outline(1.29, 1.34, 0.22, 0.73);
  join(base, lower, "edge", 0.55);
  join(lower, upper, "steel", 0.8);
  join(upper, top, "edge", 0.92);
  face(top, "steel", 0.92);
  join(
    outline(1.424, 1.474, 0.23, 0.365),
    outline(1.424, 1.474, 0.23, 0.385),
    "recess",
    0.7,
  );
  [-0.96, -0.32, 0.32, 0.96].forEach((y, index) =>
    collar(-2.14, y, 0.244, 0.87, index),
  );
  collar(1.25, 0, 0.385, 1.01, -2);
  for (const x of [-1.04, 1.04])
    for (const y of [-1.1, 1.1]) {
      const bottom = ringZ(x, y, 0.735, 0.11, 12),
        top = ringZ(x, y, 0.765, 0.092, 12);
      join(bottom, top, "edge", 0.76);
      face(top, "steel", 0.5);
      face(ringZ(x, y, 0.767, 0.039, 6), "recess", 1);
    }
  const coreBottom = ringZ(0, 0, 0.731, 0.32, 28),
    coreTop = ringZ(0, 0, 0.778, 0.285, 28);
  join(coreBottom, coreTop, "edge", 1.15);
  face(coreTop, "recess", 0.5);
  const rim = ringZ(0, 0, 0.78, 0.22, 24),
    rimInner = ringZ(0, 0, 0.783, 0.17, 24);
  rim.forEach((point, i) => {
    const n = (i + 1) % rim.length;
    face([point, rim[n], rimInner[n], rimInner[i]], "light", 0.9, -2);
  });
  return faces;
}

export function SourceJunction() {
  const [active, setActive] = useState(0);
  const canvas = useRef<HTMLCanvasElement>(null);
  const controller = useRef<Controller | null>(null);
  useLayoutEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const context = element.getContext("2d", { alpha: true });
    if (!context) return;
    const ctx = context,
      mesh = makeMesh();
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let width = 0,
      height = 0,
      selected = 0,
      visible = false,
      entered = false,
      frame = 0,
      last = 0;
    let pulseAt = -10000,
      pointerX = 0,
      pointerY = 0,
      viewX = 0,
      viewY = 0;
    const project = (point: V3, rotate = false): V3 => {
      const yaw = -0.17 + viewX * 0.045,
        pitch = 0.74 + viewY * 0.035;
      const x = point[0] * Math.cos(yaw) - point[1] * Math.sin(yaw);
      const y = point[0] * Math.sin(yaw) + point[1] * Math.cos(yaw);
      const ry = y * Math.cos(pitch) - point[2] * Math.sin(pitch);
      const depth = y * Math.sin(pitch) + point[2] * Math.cos(pitch);
      if (rotate) return [x, ry, depth];
      const scale = Math.min(width / 11.9, height / 5.6),
        perspective = 16 / (16 - depth);
      return [
        width * 0.5 + x * scale * perspective,
        height * 0.52 + ry * scale * perspective,
        depth,
      ];
    };
    const shape = (points: V3[]) => {
      ctx.beginPath();
      points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.closePath();
    };
    const colour = (base: V3, strength: number, alpha = 1) =>
      `rgba(${base.map((v) => Math.round(clamp(v * strength, 0, 255))).join(",")},${alpha})`;
    const line = (
      points: V3[],
      stroke: string,
      lineWidth: number,
      glow = 0,
    ) => {
      ctx.beginPath();
      points.forEach((point, i) => {
        const p = project(point);
        i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]);
      });
      ctx.strokeStyle = stroke;
      ctx.lineWidth = lineWidth;
      ctx.lineCap = "round";
      ctx.shadowColor = "#bf9eff";
      ctx.shadowBlur = glow;
      ctx.stroke();
      ctx.shadowBlur = 0;
    };
    const draw = (now: number) => {
      if (!visible || document.hidden || !width) return;
      ctx.clearRect(0, 0, width, height);
      const pulse = reduced.matches ? -1 : (now - pulseAt) / 2350;
      const energised =
        pulse >= 0.52 && pulse < 0.82
          ? Math.sin(((pulse - 0.52) / 0.3) * Math.PI)
          : 0;
      const floor = ctx.createRadialGradient(
        width * 0.53,
        height * 0.58,
        0,
        width * 0.53,
        height * 0.58,
        width * 0.4,
      );
      floor.addColorStop(0, `rgba(103,68,151,${0.095 + energised * 0.045})`);
      floor.addColorStop(1, "rgba(40,21,70,0)");
      ctx.fillStyle = floor;
      ctx.fillRect(0, 0, width, height);
      ctx.save();
      ctx.translate(width * 0.51, height * 0.59);
      ctx.scale(1, 0.28);
      const shadow = ctx.createRadialGradient(0, 0, 0, 0, 0, width * 0.33);
      shadow.addColorStop(0, "rgba(0,0,0,.55)");
      shadow.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = shadow;
      ctx.beginPath();
      ctx.arc(0, 0, width * 0.33, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
      const faces = mesh
        .map((face) => ({
          ...face,
          projected: face.points.map((point) => project(point)),
          viewNormal: project(face.normal, true),
        }))
        .filter((face) => face.viewNormal[2] > -0.025)
        .sort(
          (a, b) =>
            a.projected.reduce((sum, p) => sum + p[2], 0) / a.projected.length -
            b.projected.reduce((sum, p) => sum + p[2], 0) / b.projected.length,
        );
      const light = normalise([-0.32, -0.48, 1]);
      for (const face of faces) {
        shape(face.projected);
        const diffuse = Math.max(
          0,
          face.normal[0] * light[0] +
            face.normal[1] * light[1] +
            face.normal[2] * light[2],
        );
        const sheen = Math.pow(Math.max(0, face.viewNormal[2]), 7),
          lit = face.channel === selected || face.channel === -2;
        if (face.material === "light")
          ctx.fillStyle = colour(
            [191, 158, 255],
            lit ? 1 + energised * 0.12 : 0.28,
          );
        else if (face.material === "recess")
          ctx.fillStyle = colour([16, 16, 23], 0.7 + diffuse * 0.4);
        else {
          const base: V3 =
            face.material === "edge"
              ? [210, 216, 231]
              : face.material === "cable"
                ? [49, 48, 62]
                : [155, 161, 181];
          const strength = (0.34 + diffuse * 0.58 + sheen * 0.5) * face.tone;
          const xs = face.projected.map((p) => p[0]),
            ys = face.projected.map((p) => p[1]);
          const gradient = ctx.createLinearGradient(
            Math.min(...xs),
            Math.min(...ys),
            Math.max(...xs) + 0.1,
            Math.max(...ys) + 0.1,
          );
          gradient.addColorStop(0, colour(base, strength * 1.3));
          gradient.addColorStop(0.45, colour(base, strength * 0.74));
          gradient.addColorStop(1, colour(base, strength * 1.03));
          ctx.fillStyle = gradient;
        }
        ctx.fill();
        if (face.material !== "light") {
          ctx.strokeStyle =
            face.material === "edge"
              ? "rgba(210,218,239,.075)"
              : "rgba(120,119,143,.035)";
          ctx.lineWidth = 0.45;
          ctx.stroke();
        }
      }
      // Machining lines and illuminated channels follow the physical top surface.
      for (let i = 0; i < 25; i++) {
        const y = -1.03 + i * 0.087;
        if (Math.abs(y) < 0.34) continue;
        line(
          [
            [-0.87, y, 0.735],
            [0.88, y, 0.735],
          ],
          "rgba(218,220,242,.085)",
          0.55,
        );
      }
      const tracks: V3[][] = [-0.96, -0.32, 0.32, 0.96].map((y) => [
        [-1.12, y, 0.742],
        [-0.78, y, 0.742],
        [-0.46, y * 0.44, 0.742],
        [-0.3, y * 0.16, 0.742],
      ]);
      tracks.forEach((path, i) =>
        line(
          path,
          i === selected ? "rgba(191,158,255,.96)" : "rgba(149,124,187,.17)",
          i === selected ? 1.8 : 1,
          i === selected ? 9 : 0,
        ),
      );
      line(
        [
          [0.32, 0, 0.745],
          [0.78, 0, 0.745],
          [1.14, 0, 0.745],
        ],
        "rgba(191,158,255,.84)",
        1.9,
        7,
      );
      const wire = (path: V3[], radius: number, chosen: boolean) => {
        const points = Array.from({ length: 36 }, (_, i) => {
          const p = curve(path, i / 35);
          p[2] += radius * 0.83;
          return p;
        });
        line(
          points,
          chosen ? "rgba(191,158,255,.75)" : "rgba(139,130,168,.07)",
          chosen ? 1.4 : 0.6,
          chosen ? 5 : 0,
        );
      };
      inputPaths.forEach((path, i) => wire(path, 0.095, selected === i));
      wire(outputPath, 0.18, true);
      if (pulse >= 0 && pulse <= 1) {
        const incoming = pulse < 0.65,
          path = incoming ? inputPaths[selected] : outputPath,
          t = incoming ? pulse / 0.65 : (pulse - 0.65) / 0.35;
        const points = Array.from({ length: 16 }, (_, i) => {
          const p = curve(path, clamp(t - 0.095 + (i / 15) * 0.095));
          p[2] += incoming ? 0.085 : 0.16;
          return p;
        });
        line(points, "rgba(223,204,255,.97)", width < 450 ? 2 : 2.7, 13);
      }
    };
    const tick = (now: number) => {
      frame = 0;
      if (!visible || document.hidden) return;
      if (now - last < 1000 / 30) {
        frame = requestAnimationFrame(tick);
        return;
      }
      last = now;
      viewX += (pointerX - viewX) * 0.18;
      viewY += (pointerY - viewY) * 0.18;
      draw(now);
      const moving =
        Math.abs(pointerX - viewX) + Math.abs(pointerY - viewY) > 0.002;
      if (!reduced.matches && (now - pulseAt < 2420 || moving))
        frame = requestAnimationFrame(tick);
    };
    const schedule = () => {
      if (!frame && visible && !document.hidden)
        frame = requestAnimationFrame(tick);
    };
    controller.current = {
      select: (index) => {
        selected = index;
        pulseAt = performance.now();
        schedule();
      },
    };
    const resize = () => {
      const rect = element.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      const ratio = Math.min(devicePixelRatio, 1.7);
      element.width = Math.round(width * ratio);
      element.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      schedule();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    const intersection = new IntersectionObserver(
      ([entry]) => {
        visible = entry.isIntersecting;
        if (visible) {
          if (!entered) {
            entered = true;
            pulseAt = performance.now();
          }
          schedule();
        } else {
          cancelAnimationFrame(frame);
          frame = 0;
        }
      },
      { rootMargin: "80px" },
    );
    intersection.observe(element);
    const pointer = (event: PointerEvent) => {
      if (reduced.matches || event.pointerType === "touch") return;
      const rect = element.getBoundingClientRect();
      pointerX = clamp(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -1,
        1,
      );
      pointerY = clamp(
        ((event.clientY - rect.top) / rect.height) * 2 - 1,
        -1,
        1,
      );
      schedule();
    };
    const leave = () => {
      pointerX = 0;
      pointerY = 0;
      schedule();
    };
    const preference = () => {
      if (reduced.matches) pointerX = pointerY = viewX = viewY = 0;
      schedule();
    };
    const visibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(frame);
        frame = 0;
      } else schedule();
    };
    element.addEventListener("pointermove", pointer);
    element.addEventListener("pointerleave", leave);
    reduced.addEventListener("change", preference);
    document.addEventListener("visibilitychange", visibility);
    resize();
    return () => {
      controller.current = null;
      cancelAnimationFrame(frame);
      observer.disconnect();
      intersection.disconnect();
      element.removeEventListener("pointermove", pointer);
      element.removeEventListener("pointerleave", leave);
      reduced.removeEventListener("change", preference);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);
  const select = (index: number) => {
    setActive(index);
    controller.current?.select(index);
  };
  return (
    <section
      className="rivet-junction site-wrap"
      id="product"
      aria-labelledby="junction-heading"
    >
      <div className="rivet-junction-copy">
        <span className="rivet-junction-eyebrow">Built to stay connected</span>
        <h2 id="junction-heading">
          Every source.
          <br />
          <span>One connected order.</span>
        </h2>
        <p className="rivet-junction-summary">
          Connect submittals, drawing markups, and customer emails. Keep the
          communication record for each custom order together.
        </p>
        <div
          className="rivet-junction-inputs"
          role="group"
          aria-label="Explore a source connection"
        >
          {formats.map((format, index) => (
            <button
              key={format.label}
              aria-pressed={active === index}
              aria-describedby="junction-description"
              onClick={() => select(index)}
            >
              <span>{format.label}</span>
            </button>
          ))}
        </div>
        <div
          className="rivet-junction-description"
          id="junction-description"
          aria-live="polite"
          aria-atomic="true"
        >
          <strong>{formats[active].name}</strong>
          <p>{formats[active].copy}</p>
        </div>
      </div>
      <figure
        className="rivet-junction-figure"
        aria-label="Four machined source connections converge inside a metal junction into one shared conduit."
      >
        <div className="rivet-junction-figure-labels" aria-hidden="true">
          <span>04 sources</span>
          <span>01 order</span>
        </div>
        <canvas
          className="rivet-junction-canvas"
          ref={canvas}
          aria-hidden="true"
        />
        <figcaption>
          <span className="rivet-junction-indicator" />
          {formats[active].label}
          <span className="rivet-junction-caption-line" />
          Connected to the order
        </figcaption>
      </figure>
    </section>
  );
}
