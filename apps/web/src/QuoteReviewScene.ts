type Point = [number, number];
const clamp = (value: number) => Math.max(0, Math.min(1, value));
const ease = (value: number) => {
  const t = clamp(value);
  return t * t * (3 - 2 * t);
};
const phase = (p: number, start: number, end: number) =>
  ease((p - start) / (end - start));
const mix = (a: number, b: number, t: number) => a + (b - a) * t;

/** A reversible, scroll-driven illustration of the three checks in a quote review. */
export function drawQuoteReviewScene(
  c: CanvasRenderingContext2D,
  width: number,
  height: number,
  progress: number,
): void {
  if (width <= 0 || height <= 0) return;
  const p = clamp(progress);
  c.clearRect(0, 0, width, height);
  c.save();
  const pricing = phase(p, 0.28, 0.39);
  const delivery = phase(p, 0.61, 0.72);
  const arrival = phase(p, 0.005, 0.145);
  const scale = Math.min(width / 6.55, height / 3.7);
  const cx = width * 0.485;
  const cy = height * 0.67;
  const point = (x: number, y: number, z = 0): Point => [
    cx + (x + z * 0.6) * scale,
    cy + (y - z * 0.36) * scale,
  ];
  const polygon = (
    points: Point[],
    fill: string | CanvasGradient,
    stroke?: string,
  ) => {
    c.beginPath();
    points.forEach(([x, y], index) =>
      index ? c.lineTo(x, y) : c.moveTo(x, y),
    );
    c.closePath();
    c.fillStyle = fill;
    c.fill();
    if (stroke) {
      c.strokeStyle = stroke;
      c.lineWidth = 0.7;
      c.stroke();
    }
  };
  const line = (points: Point[], color: string, weight = 1) => {
    c.beginPath();
    points.forEach(([x, y], index) =>
      index ? c.lineTo(x, y) : c.moveTo(x, y),
    );
    c.strokeStyle = color;
    c.lineWidth = weight;
    c.lineJoin = "round";
    c.stroke();
  };
  const label = (
    text: string,
    x: number,
    y: number,
    size: number,
    color: string,
    align: CanvasTextAlign = "left",
  ) => {
    c.font = `500 ${size}px "IBM Plex Mono", monospace`;
    c.fillStyle = color;
    c.textAlign = align;
    c.textBaseline = "middle";
    c.fillText(text, x, y);
  };
  const glow = c.createRadialGradient(
    width * 0.53,
    height * 0.61,
    0,
    width * 0.53,
    height * 0.61,
    width * 0.53,
  );
  glow.addColorStop(0, "rgba(160,115,255,0.065)");
  glow.addColorStop(0.6, "rgba(139,105,215,0.025)");
  glow.addColorStop(1, "rgba(139,105,215,0)");
  c.fillStyle = glow;
  c.fillRect(0, 0, width, height);

  function cabinet(
    x: number,
    y: number,
    z: number,
    accent: number,
    alpha: number,
    id: number,
    unitScale = 1,
  ) {
    c.save();
    c.globalAlpha *= alpha;
    const w = 0.62 * unitScale;
    const h = 1.42 * unitScale;
    const d = 0.39 * unitScale;
    const at = (dx: number, dy: number, dz = 0) =>
      point(x + dx * unitScale, y + dy * unitScale, z + dz * unitScale);
    const left = point(x, y - h, z);
    const right = point(x + w, y, z);
    const body = c.createLinearGradient(left[0], left[1], right[0], right[1]);
    body.addColorStop(0, accent ? "#b9acd5" : "#9ea5b0");
    body.addColorStop(0.07, accent ? "#746588" : "#646b77");
    body.addColorStop(0.12, accent ? "#9583ad" : "#8b929d");
    body.addColorStop(0.38, accent ? "#5a4c6d" : "#535b65");
    body.addColorStop(0.8, accent ? "#302839" : "#30353d");
    body.addColorStop(1, "#55515f");
    const roof = c.createLinearGradient(
      left[0],
      left[1] - scale * 0.2,
      left[0] + w * scale,
      left[1],
    );
    roof.addColorStop(0, accent ? "#d8c6f7" : "#d7dce3");
    roof.addColorStop(0.4, accent ? "#a28cbf" : "#a1a9b5");
    roof.addColorStop(1, "#535362");
    const side = c.createLinearGradient(
      right[0],
      right[1],
      right[0] + d * scale,
      right[1] - h * scale,
    );
    side.addColorStop(0, "#1b1c23");
    side.addColorStop(0.6, accent ? "#5b4a74" : "#4d5361");
    side.addColorStop(1, "#8b839b");
    const shadow = c.createRadialGradient(
      ...point(x + w / 2, 0.05, z),
      0,
      ...point(x + w / 2, 0.05, z),
      scale * 0.68,
    );
    shadow.addColorStop(0, "rgba(0,0,0,0.5)");
    shadow.addColorStop(1, "rgba(0,0,0,0)");
    c.fillStyle = shadow;
    c.beginPath();
    c.ellipse(
      ...point(x + w / 2, 0.05, z),
      scale * 0.7,
      scale * 0.15,
      -0.15,
      0,
      Math.PI * 2,
    );
    c.fill();
    polygon(
      [
        point(x, y - h, z),
        point(x, y - h, z + d),
        point(x + w, y - h, z + d),
        point(x + w, y - h, z),
      ],
      roof,
      "#b8afc65c",
    );
    polygon(
      [
        point(x + w, y - h, z),
        point(x + w, y - h, z + d),
        point(x + w, y, z + d),
        point(x + w, y, z),
      ],
      side,
      "#a4a7b537",
    );
    polygon(
      [
        at(0.028, -1.42),
        at(0.59, -1.42),
        at(0.62, -1.39),
        at(0.62, -0.027),
        at(0.59, 0),
        at(0.027, 0),
        at(0, -0.027),
        at(0, -1.39),
      ],
      body,
      "#e5deef70",
    );
    const inner = c.createLinearGradient(
      ...at(0.04, -1.29),
      ...at(0.55, -0.12),
    );
    inner.addColorStop(0, "#282a34");
    inner.addColorStop(0.4, accent ? "#393142" : "#333741");
    inner.addColorStop(1, "#20212a");
    polygon(
      [at(0.05, -1.31), at(0.56, -1.31), at(0.56, -0.12), at(0.05, -0.12)],
      inner,
      "#b8bcc329",
    );
    line([at(0.07, -1.28), at(0.54, -1.28)], "#ddd8e968");
    // Inset meter, louvres and a door catch make these equipment rather than blocks.
    polygon(
      [at(0.115, -1.15), at(0.49, -1.15), at(0.49, -0.91), at(0.115, -0.91)],
      "#0b0d13",
      "#c0b5d54a",
    );
    line(
      [at(0.15, -1.075), at(0.3, -1.075)],
      accent ? "#d7bcff" : "#adb4c6",
      1.4,
    );
    line([at(0.15, -1.02), at(0.25, -1.02)], "#75788a", 1);
    for (let i = 0; i < 6; i++) {
      const vent = -0.75 + i * 0.073;
      line(
        [at(0.12, vent), at(0.45, vent)],
        "#101119",
        Math.max(1, scale * 0.013),
      );
      line([at(0.12, vent + 0.015), at(0.45, vent + 0.015)], "#aba9ba38", 0.65);
    }
    line(
      [at(0.505, -0.69), at(0.505, -0.48)],
      "#bfc0cfb3",
      Math.max(1, scale * 0.014),
    );
    for (const [sx, sy] of [
      [0.025, -1.365],
      [0.59, -1.365],
      [0.025, -0.055],
      [0.59, -0.055],
    ]) {
      const screw = at(sx, sy);
      c.beginPath();
      c.arc(screw[0], screw[1], Math.max(0.7, scale * 0.012), 0, Math.PI * 2);
      c.fillStyle = "#10131a";
      c.fill();
      line(
        [
          [screw[0] - 0.5, screw[1]],
          [screw[0] + 0.5, screw[1]],
        ],
        "#d2cdda8c",
        0.5,
      );
    }
    line(
      [at(0.06, -0.045), at(0.55, -0.045)],
      accent ? "#cfaaff" : "#b9b9cd74",
      1.2,
    );
    if (scale * unitScale > 63)
      label(
        String(id).padStart(2, "0"),
        ...at(0.31, -0.225),
        Math.max(7, scale * unitScale * 0.09),
        "#c5c3d1",
        "center",
      );
    c.restore();
  }

  // Eight quoted cabinets remain grounded; two new units settle into the vacant bays.
  c.save();
  c.globalAlpha = 1 - delivery;
  const mainAlpha = 1 - pricing * 0.86;
  const columns = 5;
  for (let row = 0; row < 2; row++) {
    for (let column = 0; column < columns; column++) {
      const i = row * columns + column;
      const extra = i >= 8;
      const x = (column - 2) * 0.97 - 0.44;
      const z = row === 0 ? 1.32 : 0;
      if (extra) {
        const a = point(x - 0.035, 0.045, z - 0.03);
        const b = point(x + 0.665, 0.045, z - 0.03);
        const d = point(x - 0.035, 0.045, z + 0.47);
        const e = point(x + 0.665, 0.045, z + 0.47);
        c.save();
        c.globalAlpha *= mainAlpha * (1 - arrival * 0.7);
        c.setLineDash([3, 4]);
        line([a, b, e, d, a], "#ba9deaa6");
        c.restore();
      }
      if (i === 6) continue;
      cabinet(
        x,
        extra ? -(1 - arrival) * 1.05 : 0,
        z,
        extra ? 1 : 0,
        mainAlpha * (extra ? arrival : 1),
        i + 1,
      );
    }
  }
  // The inspected unit leaves the array so the price has a clear physical subject.
  cabinet(
    mix(-1.41, -1.95, pricing),
    mix(0, 0.25, pricing),
    0,
    pricing,
    1,
    7,
    mix(1, 1.53, pricing),
  );
  if (arrival > 0 && pricing < 1) {
    c.save();
    c.globalAlpha = arrival * (1 - pricing) * (1 - delivery);
    const a = point(0.5, 0.26),
      b = point(2.2, 0.26);
    line([a, [a[0], a[1] + 5], [b[0], a[1] + 5], b], "#bb9bed8c");
    label(
      "Rev B → Rev C",
      (a[0] + b[0]) / 2,
      a[1] + 21,
      Math.max(12, scale * 0.135),
      "#ccb4f2",
      "center",
    );
    c.restore();
  }
  if (pricing > 0) {
    c.save();
    c.globalAlpha *= pricing;
    const start = point(-0.95, -0.47);
    const elbow = point(-0.33, -0.47);
    const end = point(0.02, -0.99);
    line([start, elbow, [elbow[0], end[1]], end], "#b6a0d58f", 1.1);
    c.beginPath();
    c.arc(...start, 2.5, 0, Math.PI * 2);
    c.fillStyle = "#c7a3ff";
    c.fill();
    const labelX = point(0.06, 0)[0];
    label("MSB-01 / REV C", labelX, point(0, -1.58)[1], 12, "#a7a6b9");
    c.font = `500 ${Math.min(width * 0.069, scale * 0.52)}px "Sora", sans-serif`;
    c.fillStyle = "#ece5f7";
    c.textAlign = "left";
    c.fillText("65 kA", labelX, point(0, -1.06)[1]);
    line(
      [
        [labelX, point(0, -0.72)[1]],
        [point(2.45, 0)[0], point(0, -0.72)[1]],
      ],
      "#aa93cd47",
    );
    label(
      "DRAWING · SOURCE EVIDENCE",
      labelX,
      point(0, -0.49)[1],
      12,
      "#bda2df",
    );
    c.restore();
  }
  c.restore();

  if (delivery > 0) {
    c.save();
    c.globalAlpha = delivery;
    const left = width * 0.11;
    const right = width * 0.89;
    const railWidth = right - left;
    const top = height * 0.39;
    const bottom = height * 0.63;
    const u = (rating: number) => left + (railWidth * rating) / 85;
    const depth = Math.min(12, height * 0.032);
    const rail = (y: number, endWeek: number, tint: boolean) => {
      const end = u(endWeek);
      const metal = c.createLinearGradient(left, y, left, y + depth);
      metal.addColorStop(0, tint ? "#d2baed" : "#c9cbd4");
      metal.addColorStop(0.13, tint ? "#a28aba" : "#939ba8");
      metal.addColorStop(0.42, tint ? "#59466d" : "#4e5460");
      metal.addColorStop(0.8, "#242630");
      metal.addColorStop(1, tint ? "#9b7fbb" : "#858899");
      polygon(
        [
          [left, y],
          [end, y],
          [end + depth * 0.6, y - depth * 0.5],
          [left + depth * 0.6, y - depth * 0.5],
        ],
        tint ? "#ab93c8" : "#b5b9c6",
        "#e8dff36b",
      );
      polygon(
        [
          [left, y],
          [end, y],
          [end, y + depth],
          [left, y + depth],
        ],
        metal,
        "#d4c4ec47",
      );
      polygon(
        [
          [end, y],
          [end + depth * 0.6, y - depth * 0.5],
          [end + depth * 0.6, y + depth * 0.5],
          [end, y + depth],
        ],
        "#54445f",
      );
      for (let i = 0; i <= endWeek * 2; i++) {
        const x = u(i / 2);
        line(
          [
            [x, y + 0.5],
            [x, y + (i % 4 === 0 ? depth * 0.65 : depth * 0.3)],
          ],
          "#171722c2",
          0.7,
        );
      }
    };
    rail(top, 85, false);
    rail(bottom, mix(85, 65, phase(p, 0.69, 0.84)), true);
    const overlap = phase(p, 0.73, 0.85);
    c.save();
    c.globalAlpha *= overlap;
    const area = c.createLinearGradient(u(65), 0, u(85), 0);
    area.addColorStop(0, "#b085ef09");
    area.addColorStop(1, "#b085ef2b");
    c.fillStyle = area;
    c.fillRect(u(65), top - 18, u(85) - u(65), bottom - top + depth + 32);
    c.setLineDash([3, 5]);
    line(
      [
        [u(65), top - 20],
        [u(65), bottom + depth + 18],
      ],
      "#cdc2e98c",
    );
    line(
      [
        [u(85), top - 20],
        [u(85), bottom + depth + 18],
      ],
      "#c3a2ec9c",
    );
    c.setLineDash([]);
    label(
      "85",
      u(85),
      top - 34,
      Math.max(13, width * 0.025),
      "#f0eafa",
      "center",
    );
    label(
      "65",
      u(65),
      bottom + depth + 29,
      Math.max(12, width * 0.022),
      "#e1c8ff",
      "center",
    );
    c.restore();
    label("REQUESTED CHANGE", left, top - 25, 12, "#bdc0cb");
    label("CURRENT DRAWING", left, bottom - 25, 12, "#c3a9e6");
    label("RECORDED RATING CHANGE / kA", left, height * 0.88, 12, "#858391");
    c.restore();
  }
  c.restore();
}
