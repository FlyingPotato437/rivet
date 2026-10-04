import { useLayoutEffect, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import "./rivet-assembly.css";
gsap.registerPlugin(ScrollTrigger);
type V3 = [number, number, number];
type P2 = [number, number];
type Material = "metal" | "bevel" | "inner" | "light" | "bolt" | "core";
type Face = { vertices: V3[]; material: Material; tone: number };
type ViewFace = Face & { normal: V3; depth: number; projected: P2[] };
type AssemblyState = { progress: number; pointerX: number; pointerY: number };
const clamp = (value: number, min = 0, max = 1) =>
  Math.min(max, Math.max(min, value));
const smooth = (value: number) => {
  const t = clamp(value);
  return t * t * (3 - 2 * t);
};
const normalize = ([x, y, z]: V3): V3 => {
  const length = Math.hypot(x, y, z) || 1;
  return [x / length, y / length, z / length];
};
const faceNormal = (points: V3[]): V3 => {
  const a = points[0],
    b = points[1],
    c = points[2];
  const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  return normalize([
    u[1] * v[2] - u[2] * v[1],
    u[2] * v[0] - u[0] * v[2],
    u[0] * v[1] - u[1] * v[0],
  ]);
};
function octagon(radius: number, chamfer = 0.28): P2[] {
  return [
    [-radius + chamfer, -radius],
    [radius - chamfer, -radius],
    [radius, -radius + chamfer],
    [radius, radius - chamfer],
    [radius - chamfer, radius],
    [-radius + chamfer, radius],
    [-radius, radius - chamfer],
    [-radius, -radius + chamfer],
  ];
}
function perimeter(points: P2[], z: number): V3[] {
  return points.map(([x, y]) => [x, y, z]);
}
function join(
  faces: Face[],
  lower: V3[],
  upper: V3[],
  material: Material,
  tone: number,
  inward = false,
) {
  lower.forEach((point, index) => {
    const next = (index + 1) % lower.length;
    const vertices = [point, lower[next], upper[next], upper[index]];
    faces.push({
      vertices: inward ? vertices.reverse() : vertices,
      material,
      tone,
    });
  });
}
function extrude(
  faces: Face[],
  points: P2[],
  bottom: number,
  top: number,
  material: Material,
  tone: number,
) {
  const lower = perimeter(points, bottom);
  const upper = perimeter(points, top);
  join(faces, lower, upper, material, tone);
  faces.push({ vertices: upper, material, tone });
}
function cylinder(
  faces: Face[],
  x: number,
  y: number,
  radius: number,
  bottom: number,
  top: number,
  sides: number,
  material: Material,
  tone = 1,
  rotation = 0,
) {
  const points: P2[] = Array.from({ length: sides }, (_, i) => {
    const angle = (i / sides) * Math.PI * 2 + rotation;
    return [x + Math.cos(angle) * radius, y + Math.sin(angle) * radius];
  });
  extrude(faces, points, bottom, top, material, tone);
}
function ring(faces: Face[], z: number, tone: number) {
  const radius = 2.36,
    hole = 1.69,
    half = 0.16,
    bevel = 0.045;
  const outerLower = perimeter(octagon(radius), z - half + bevel);
  const outerUpper = perimeter(octagon(radius), z + half - bevel);
  const outerTop = perimeter(octagon(radius - bevel), z + half);
  const innerUpper = perimeter(octagon(hole, 0.19), z + half - bevel);
  const innerLower = perimeter(octagon(hole, 0.19), z - half);
  const innerTop = perimeter(octagon(hole + bevel, 0.19), z + half);
  join(faces, outerLower, outerUpper, "metal", tone * 0.8);
  join(faces, outerUpper, outerTop, "bevel", tone);
  join(
    faces,
    perimeter(octagon(radius - bevel), z - half),
    outerLower,
    "bevel",
    tone * 0.6,
  );
  join(faces, innerLower, innerUpper, "inner", tone, true);
  join(faces, innerUpper, innerTop, "bevel", tone * 0.72, true);
  outerTop.forEach((point, i) => {
    const next = (i + 1) % outerTop.length;
    faces.push({
      vertices: [point, outerTop[next], innerTop[next], innerTop[i]],
      material: "metal",
      tone,
    });
  });
  join(
    faces,
    perimeter(octagon(radius + 0.002), z - 0.032),
    perimeter(octagon(radius + 0.002), z - 0.003),
    "light",
    0.8,
  );
  const rim = perimeter(octagon(radius - 0.12), z + half + 0.001);
  const rimInside = perimeter(octagon(radius - 0.139), z + half + 0.001);
  rim.forEach((point, i) => {
    const next = (i + 1) % rim.length;
    faces.push({
      vertices: [point, rim[next], rimInside[next], rimInside[i]],
      material: "light",
      tone: 0.62,
    });
  });
  for (const x of [-2.03, 2.03]) {
    for (const y of [-2.03, 2.03]) {
      cylinder(
        faces,
        x,
        y,
        0.167,
        z + half,
        z + half + 0.007,
        16,
        "inner",
        0.25,
      );
      cylinder(
        faces,
        x,
        y,
        0.106,
        z + half + 0.008,
        z + half + 0.01,
        12,
        "bolt",
        0.55,
      );
    }
  }
}
function transformGroup(faces: Face[], turn: number, x: number, y: number) {
  const cosine = Math.cos(turn),
    sine = Math.sin(turn);
  faces.forEach((face) => {
    face.vertices = face.vertices.map(([px, py, pz]) => [
      px * cosine - py * sine + x,
      px * sine + py * cosine + y,
      pz,
    ]);
  });
}
function assemblyMesh(progress: number): Face[] {
  const faces: Face[] = [];
  const connection = smooth((progress - 0.03) / 0.78);
  const spread = 1 - connection;
  const locked = smooth((progress - 0.72) / 0.28);
  const heights = [-0.4 - spread * 0.48, 0, 0.4 + spread * 0.85];
  heights.forEach((height, index) => {
    const layer: Face[] = [];
    ring(layer, height, [0.68, 0.43, 1][index]);
    if (index === 2) {
      for (const x of [-2.03, 2.03]) {
        for (const y of [-2.03, 2.03]) {
          const head = height + 0.21 + (1 - locked) * 0.38;
          cylinder(layer, x, y, 0.063, head - 0.5, head, 12, "bolt", 0.9);
          for (let thread = 0; thread < 6; thread++) {
            const z = head - 0.45 + thread * 0.055;
            cylinder(layer, x, y, 0.079, z, z + 0.013, 12, "metal", 0.6);
          }
          cylinder(
            layer,
            x,
            y,
            0.159,
            head - 0.012,
            head + 0.013,
            16,
            "bevel",
            1.1,
          );
          cylinder(
            layer,
            x,
            y,
            0.135,
            head + 0.012,
            head + 0.115,
            6,
            "bolt",
            1.1,
            (1 - locked) * 1.8,
          );
          cylinder(
            layer,
            x,
            y,
            0.056,
            head + 0.116,
            head + 0.117,
            6,
            "inner",
            0.18,
            (1 - locked) * 1.8,
          );
        }
      }
    }
    transformGroup(
      layer,
      [0.085, -0.035, -0.08][index] * spread,
      [-0.38, 0, 0.42][index] * spread,
      [0.12, 0, -0.1][index] * spread,
    );
    faces.push(...layer);
  });
  for (let arm = 0; arm < 4; arm++) {
    const support: Face[] = [];
    extrude(
      support,
      [
        [0.7, -0.09],
        [1.77, -0.09],
        [1.77, 0.09],
        [0.7, 0.09],
      ],
      -0.04,
      0.05,
      "metal",
      0.4,
    );
    transformGroup(support, (arm * Math.PI) / 2, 0, 0);
    faces.push(...support);
  }
  extrude(faces, octagon(0.87, 0.15), 0.01, 0.2, "metal", 0.58);
  extrude(faces, octagon(0.81, 0.12), 0.2, 0.235, "light", 0.5);
  extrude(faces, octagon(0.785, 0.115), 0.235, 0.265, "core", 0.8);
  const mark: P2[][] = [
    [
      [5, 5],
      [19.5, 5],
      [19.5, 11],
      [11, 11],
      [11, 17],
      [19.5, 17],
      [19.5, 27],
      [5, 27],
    ],
    [
      [19.5, 11],
      [27, 11],
      [27, 17],
      [19.5, 17],
    ],
    [
      [19.5, 17],
      [25, 17],
      [29, 27],
      [22, 27],
    ],
  ];
  mark.forEach((polygon) =>
    extrude(
      faces,
      polygon.map(([x, y]) => [(x - 17) * 0.049, (y - 16) * 0.049]),
      0.266,
      0.345,
      "light",
      1.15,
    ),
  );
  return faces;
}
function renderAssembly(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  state: AssemblyState,
) {
  const c = context;
  c.clearRect(0, 0, width, height);
  const tilt = 1.03 + state.pointerY * 0.075;
  const turn = -0.58 + state.pointerX * 0.075;
  const cy = Math.cos(turn),
    sy = Math.sin(turn),
    ct = Math.cos(tilt),
    st = Math.sin(tilt);
  const mesh = assemblyMesh(state.progress);
  const view: ViewFace[] = [];
  let minX = Infinity,
    maxX = -Infinity,
    minY = Infinity,
    maxY = -Infinity;
  for (const face of mesh) {
    const vertices: V3[] = face.vertices.map(([x, y, z]) => {
      const rotatedX = x * cy - y * sy,
        rotatedY = x * sy + y * cy;
      return [rotatedX, rotatedY * ct - z * st, rotatedY * st + z * ct];
    });
    const normal = faceNormal(vertices);
    if (normal[2] < 0.005) continue;
    const projected: P2[] = vertices.map(([x, y, z]) => {
      const perspective = 14 / (14 - z);
      const px = x * perspective,
        py = y * perspective;
      minX = Math.min(minX, px);
      maxX = Math.max(maxX, px);
      minY = Math.min(minY, py);
      maxY = Math.max(maxY, py);
      return [px, py];
    });
    view.push({
      ...face,
      vertices,
      normal,
      projected,
      depth:
        vertices.reduce((sum, point) => sum + point[2], 0) / vertices.length,
    });
  }
  view.sort((a, b) => a.depth - b.depth);
  const mobile = width < 700;
  const topY = 12;
  const floorY = height - 30;
  const scale = Math.min(
    (width * (mobile ? 0.94 : 0.79)) / (maxX - minX),
    (floorY - topY) / (maxY - minY),
  );
  const offsetX = width / 2 - ((minX + maxX) / 2) * scale;
  const offsetY = (topY + floorY) / 2 - ((minY + maxY) / 2) * scale;
  const sceneWidth = (maxX - minX) * scale;
  const glow = c.createRadialGradient(
    width / 2,
    height * 0.55,
    0,
    width / 2,
    height * 0.55,
    sceneWidth * 0.57,
  );
  glow.addColorStop(0, "rgba(152,112,245,0.105)");
  glow.addColorStop(0.5, "rgba(105,76,167,0.035)");
  glow.addColorStop(1, "rgba(105,76,167,0)");
  c.fillStyle = glow;
  c.fillRect(0, 0, width, height);
  const shadowY = floorY + 6;
  const shadow = c.createRadialGradient(
    width / 2,
    shadowY,
    0,
    width / 2,
    shadowY,
    sceneWidth * 0.49,
  );
  shadow.addColorStop(0, "rgba(0,0,0,0.56)");
  shadow.addColorStop(1, "rgba(0,0,0,0)");
  c.save();
  c.translate(width / 2, shadowY);
  c.scale(1, 0.06);
  c.translate(-width / 2, -shadowY);
  c.fillStyle = shadow;
  c.fillRect(
    width / 2 - sceneWidth,
    shadowY - sceneWidth,
    sceneWidth * 2,
    sceneWidth * 2,
  );
  c.restore();
  const light = normalize([-0.45, -0.68, 0.8]);
  const halfLight = normalize([light[0], light[1], light[2] + 1]);
  for (const face of view) {
    const points = face.projected.map(([x, y]): P2 => [
      x * scale + offsetX,
      y * scale + offsetY,
    ]);
    const diffuse = Math.max(
      0,
      face.normal.reduce((sum, value, i) => sum + value * light[i], 0),
    );
    const specular = Math.pow(
      Math.max(
        0,
        face.normal.reduce((sum, value, i) => sum + value * halfLight[i], 0),
      ),
      24,
    );
    c.beginPath();
    points.forEach(([x, y], index) =>
      index === 0 ? c.moveTo(x, y) : c.lineTo(x, y),
    );
    c.closePath();
    const bounds = points.reduce(
      (box, [x, y]) => ({
        left: Math.min(box.left, x),
        top: Math.min(box.top, y),
        right: Math.max(box.right, x),
        bottom: Math.max(box.bottom, y),
      }),
      { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity },
    );
    const gradient = c.createLinearGradient(
      bounds.left,
      bounds.top,
      bounds.right + 0.1,
      bounds.bottom + 0.1,
    );
    if (face.material === "light") {
      const luminance = 0.75 + face.tone * 0.25;
      gradient.addColorStop(0, `rgba(218,204,255,${luminance})`);
      gradient.addColorStop(0.4, "#bf9eff");
      gradient.addColorStop(1, "#9870f5");
      c.fillStyle = gradient;
      c.shadowColor = "rgba(152,112,245,0.45)";
      c.shadowBlur = face.tone > 1 ? 12 : 4;
      c.fill();
      c.shadowBlur = 0;
      continue;
    }
    const inner = face.material === "inner";
    const core = face.material === "core";
    const bevel = face.material === "bevel";
    const base = inner ? [30, 32, 41] : core ? [24, 25, 34] : [120, 125, 141];
    const shade = (reflection: number) => {
      const strength = (0.4 + diffuse * 0.65) * face.tone * reflection;
      const shine = (bevel ? 42 : 12) + specular * (inner || core ? 12 : 95);
      return `rgb(${base.map((channel) => Math.round(clamp(channel * strength + shine, 0, 255))).join(",")})`;
    };
    gradient.addColorStop(0, shade(0.61));
    gradient.addColorStop(0.24, shade(0.91));
    gradient.addColorStop(0.43, shade(bevel ? 1.9 : 1.58));
    gradient.addColorStop(0.5, shade(1.05));
    gradient.addColorStop(0.73, shade(0.72));
    gradient.addColorStop(1, shade(0.9));
    c.fillStyle = gradient;
    c.fill();
    c.strokeStyle = `rgba(223,227,242,${inner ? 0.035 : bevel ? 0.19 : 0.075})`;
    c.lineWidth = 0.65;
    c.stroke();
  }
}
export function RivetAssembly() {
  const section = useRef<HTMLElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  useLayoutEffect(() => {
    const element = canvas.current;
    const stage = section.current;
    const context = element?.getContext("2d", { alpha: true });
    if (!element || !stage || !context) return;
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const state: AssemblyState = {
      progress: media.matches ? 1 : 0,
      pointerX: 0,
      pointerY: 0,
    };
    const target = { x: 0, y: 0 };
    let width = 0,
      height = 0,
      frame = 0,
      last = 0,
      visible = false,
      disposed = false;
    const queueDraw = () => {
      if (!frame && visible && !document.hidden && !disposed) {
        frame = requestAnimationFrame(draw);
      }
    };
    const draw = (time: number) => {
      frame = 0;
      if (!visible || document.hidden || !width || disposed) return;
      if (time - last < 1000 / 30) {
        queueDraw();
        return;
      }
      last = time;
      state.pointerX += (target.x - state.pointerX) * 0.09;
      state.pointerY += (target.y - state.pointerY) * 0.09;
      renderAssembly(context, width, height, state);
      if (
        Math.abs(target.x - state.pointerX) +
          Math.abs(target.y - state.pointerY) >
        0.002
      ) {
        queueDraw();
      }
    };
    const resize = () => {
      const bounds = element.getBoundingClientRect();
      width = bounds.width;
      height = bounds.height;
      const dpr = Math.min(window.devicePixelRatio || 1, 1.6);
      element.width = Math.round(width * dpr);
      element.height = Math.round(height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      queueDraw();
    };
    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(element);
    const visibilityObserver = new IntersectionObserver(
      ([entry]) => {
        visible = entry.isIntersecting;
        if (visible) queueDraw();
        else {
          cancelAnimationFrame(frame);
          frame = 0;
        }
      },
      { rootMargin: "80px" },
    );
    visibilityObserver.observe(stage);
    const onVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(frame);
        frame = 0;
      } else queueDraw();
    };
    const move = (event: PointerEvent) => {
      if (media.matches || event.pointerType !== "mouse") return;
      const bounds = element.getBoundingClientRect();
      target.x = clamp(
        (event.clientX - bounds.left) / bounds.width - 0.5,
        -0.5,
        0.5,
      );
      target.y = clamp(
        (event.clientY - bounds.top) / bounds.height - 0.5,
        -0.5,
        0.5,
      );
      queueDraw();
    };
    const leave = () => {
      target.x = target.y = 0;
      queueDraw();
    };
    stage.addEventListener("pointermove", move, { passive: true });
    stage.addEventListener("pointerleave", leave);
    document.addEventListener("visibilitychange", onVisibility);
    const motion = gsap.matchMedia();
    motion.add(
      {
        animated: "(prefers-reduced-motion: no-preference)",
        reduced: "(prefers-reduced-motion: reduce)",
      },
      (motionContext) => {
        const animated = motionContext.conditions?.animated;
        state.progress = animated ? 0 : 1;
        state.pointerX = state.pointerY = target.x = target.y = 0;
        queueDraw();
        if (!animated) return;
        const tween = gsap.to(state, {
          progress: 1,
          ease: "none",
          onUpdate: queueDraw,
          scrollTrigger: {
            trigger: stage,
            start: "top 75%",
            end: "bottom 40%",
            scrub: 0.85,
            invalidateOnRefresh: true,
          },
        });
        return () => {
          tween.scrollTrigger?.kill();
          tween.kill();
        };
      },
    );
    resize();
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      motion.revert();
      resizeObserver.disconnect();
      visibilityObserver.disconnect();
      stage.removeEventListener("pointermove", move);
      stage.removeEventListener("pointerleave", leave);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);
  return (
    <section
      id="workflow"
      className="rivet-assembly"
      ref={section}
      aria-labelledby="rivet-assembly-heading"
    >
      <div className="rivet-assembly-heading">
        <h2 id="rivet-assembly-heading">
          From returned comments
          <br />
          to a complete record.
        </h2>
        <p>
          Collect the comments, confirm their drawing locations, and keep the
          responses and approvals attached to every revision.
        </p>
      </div>
      <div className="rivet-assembly-stage">
        <canvas
          ref={canvas}
          className="rivet-assembly-canvas"
          aria-hidden="true"
        />
      </div>
      <div className="rivet-assembly-caption">
        <span>Sources</span>
        <i aria-hidden="true" />
        <span>Comments</span>
        <i aria-hidden="true" />
        <span>Revisions</span>
      </div>
      <ol className="rivet-assembly-steps">
        {[
          ["Import", "Add the submittal, marked-up PDFs, and saved emails."],
          [
            "Review",
            "Confirm each comment, author, date, and drawing location.",
          ],
          [
            "Record",
            "Keep responses and confirmed changes linked to their comments.",
          ],
          [
            "Share",
            "Approve a record and prepare notices for customer and production teams.",
          ],
        ].map(([title, copy], index) => (
          <li key={title}>
            <span aria-hidden="true">0{index + 1}</span>
            <div>
              <strong>{title}</strong>
              <p>{copy}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
