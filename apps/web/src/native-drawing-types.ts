/** Coordinates come from the saved native drawing, never a browser layout. */
export type NativePoint = [number, number];
export type NativeTerminal = {
  id: string;
  name?: string;
  position: NativePoint;
};
export type NativeDevice = {
  tag: string;
  bounds: [number, number, number, number];
  terminals?: NativeTerminal[];
  position?: NativePoint;
};
export type NativeWire = {
  id: string;
  from?: string;
  to?: string;
  net_id?: string;
  actual_from?: string | null;
  actual_to?: string | null;
  points: NativePoint[];
};
export type NativeDrawing = {
  units?: string;
  devices: NativeDevice[];
  wires: NativeWire[];
  enclosures?: {
    id: string;
    bounds: [number, number, number, number];
    points?: NativePoint[];
  }[];
  junctions?: { id: string; position: NativePoint; net_id?: string }[];
  crossings?: {
    wire_ids: string[];
    position: NativePoint;
    connected: boolean;
    kind?: string;
  }[];
};
export type NativeChange = {
  id?: string;
  request_id?: string;
  kind: string;
  device_tag?: string;
  wire_id?: string;
  label?: string;
  reason?: string;
  before?: unknown;
  after?: unknown;
};
export type NativeCheck = {
  id?: string;
  status: string;
  label?: string;
  reason?: string;
  device_tag?: string;
  wire_id?: string;
};
export type NativeVerification = {
  status: string;
  checks?: NativeCheck[];
  before?: NativeDrawing;
  after?: NativeDrawing;
  diff?: { changes?: NativeChange[] };
  changes?: NativeChange[];
  blockers?: { reason?: string; message?: string }[];
};

const labels: Record<string, string> = {
  move_device: "Device moved",
  resize_device: "Device resized",
  add_device: "Device added",
  remove_device: "Device removed",
  rewire: "Connection changed",
  reroute: "Wire rerouted",
  add_wire: "Wire added",
  remove_wire: "Wire removed",
};
export function nativeChangeLabel(change: NativeChange) {
  if (!change.label && change.kind === "device_geometry") {
    const before = nativeRecord(change.before),
      after = nativeRecord(change.after);
    const moved =
        JSON.stringify(before.position) !== JSON.stringify(after.position),
      resized = before.width !== after.width || before.height !== after.height,
      rotated = before.rotation !== after.rotation;
    return [moved, resized, rotated].filter(Boolean).length > 1
      ? "Layout updated"
      : moved
        ? "Device moved"
        : resized
          ? "Device resized"
          : rotated
            ? "Device rotated"
            : "Device values updated";
  }
  return (
    change.label || labels[change.kind] || change.kind.replaceAll("_", " ")
  );
}
export function nativeChangeTarget(change: NativeChange) {
  return change.device_tag || change.wire_id || "Drawing";
}
export function nativeChanges(check?: NativeVerification) {
  return check?.diff?.changes || check?.changes || [];
}
export function nativeChecksPass(check?: NativeVerification) {
  return (
    !!check &&
    check.status === "verified" &&
    !!check.checks?.length &&
    !check.blockers?.length &&
    check.checks.every((item) => item.status === "pass")
  );
}

function finite(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    Math.abs(value) <= 1e9
  );
}
function validPoint(point: unknown): point is NativePoint {
  return Array.isArray(point) && point.length === 2 && point.every(finite);
}

/** Refuse a partial diagram rather than inventing missing geometry. */
export function nativeDrawingValid(
  drawing?: NativeDrawing,
): drawing is NativeDrawing {
  if (
    !drawing ||
    !Array.isArray(drawing.devices) ||
    !Array.isArray(drawing.wires)
  )
    return false;
  if (
    drawing.junctions &&
    (!Array.isArray(drawing.junctions) ||
      drawing.junctions.some(
        (junction) => !junction.id || !validPoint(junction.position),
      ))
  )
    return false;
  if (
    drawing.crossings &&
    (!Array.isArray(drawing.crossings) ||
      drawing.crossings.some(
        (crossing) =>
          !Array.isArray(crossing.wire_ids) ||
          !validPoint(crossing.position) ||
          typeof crossing.connected !== "boolean",
      ))
  )
    return false;
  if (drawing.devices.length > 500 || drawing.wires.length > 2000) return false;
  const deviceTags = new Set<string>(),
    wireIds = new Set<string>();
  let pointCount = 0;
  for (const device of drawing.devices) {
    if (!device.tag || deviceTags.has(device.tag)) return false;
    deviceTags.add(device.tag);
    if (
      !Array.isArray(device.bounds) ||
      device.bounds.length !== 4 ||
      !device.bounds.every(finite)
    )
      return false;
    if (
      device.bounds[2] <= device.bounds[0] ||
      device.bounds[3] <= device.bounds[1]
    )
      return false;
    if (
      device.terminals &&
      (!Array.isArray(device.terminals) ||
        device.terminals.some(
          (terminal) => !terminal.id || !validPoint(terminal.position),
        ))
    )
      return false;
    if (
      device.terminals &&
      new Set(device.terminals.map((terminal) => terminal.id)).size !==
        device.terminals.length
    )
      return false;
    pointCount += 2 + (device.terminals?.length || 0);
  }
  for (const wire of drawing.wires) {
    if (!wire.id || wireIds.has(wire.id)) return false;
    wireIds.add(wire.id);
    if (
      !Array.isArray(wire.points) ||
      wire.points.length < 2 ||
      wire.points.length > 10000 ||
      !wire.points.every(validPoint)
    )
      return false;
    pointCount += wire.points.length;
    if (pointCount > 100_000) return false;
  }
  if (
    drawing.enclosures &&
    (!Array.isArray(drawing.enclosures) ||
      drawing.enclosures.some(
        (enclosure) =>
          !enclosure.id ||
          !Array.isArray(enclosure.bounds) ||
          enclosure.bounds.length !== 4 ||
          !enclosure.bounds.every(finite) ||
          enclosure.bounds[2] <= enclosure.bounds[0] ||
          enclosure.bounds[3] <= enclosure.bounds[1] ||
          (enclosure.points && !enclosure.points.every(validPoint)),
      ))
  )
    return false;
  return !!(drawing.devices.length || drawing.wires.length);
}

export type NativeViewport = {
  width: number;
  height: number;
  unitScale: number;
  point: (point: NativePoint) => NativePoint;
};

/** Both revisions use the same extent: movement cannot be hidden by auto-fit. */
export function nativeViewport(
  before?: NativeDrawing,
  after?: NativeDrawing,
): NativeViewport | null {
  if (!nativeDrawingValid(before) || !nativeDrawingValid(after)) return null;
  if (before.units !== after.units) return null;
  const points: NativePoint[] = [];
  for (const drawing of [before, after]) {
    for (const device of drawing.devices) {
      points.push(
        [device.bounds[0], device.bounds[1]],
        [device.bounds[2], device.bounds[3]],
      );
      for (const terminal of device.terminals || [])
        points.push(terminal.position);
    }
    for (const wire of drawing.wires) points.push(...wire.points);
    for (const enclosure of drawing.enclosures || [])
      points.push(
        [enclosure.bounds[0], enclosure.bounds[1]],
        [enclosure.bounds[2], enclosure.bounds[3]],
      );
    for (const junction of drawing.junctions || [])
      points.push(junction.position);
  }
  let minX = Infinity,
    maxX = -Infinity,
    minY = Infinity,
    maxY = -Infinity;
  for (const [x, y] of points) {
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  }
  if (maxX <= minX || maxY <= minY) return null;
  const width = 1000,
    height = 580,
    margin = 65;
  const unitScale = Math.min(
    (width - margin * 2) / (maxX - minX),
    (height - margin * 2) / (maxY - minY),
  );
  const left = (width - (maxX - minX) * unitScale) / 2;
  const top = (height - (maxY - minY) * unitScale) / 2;
  return {
    width,
    height,
    unitScale,
    point: ([x, y]) => [
      left + (x - minX) * unitScale,
      top + (maxY - y) * unitScale,
    ],
  };
}

export function nativeChangeDescription(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number")
    return String(value);
  if (Array.isArray(value))
    return value.map(nativeChangeDescription).join(", ");
  if (typeof value === "object") {
    return Object.entries(value)
      .map(
        ([key, v]) =>
          `${key.replaceAll("_", " ")}: ${nativeChangeDescription(v)}`,
      )
      .join(" · ");
  }
  return String(value);
}

function nativeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function coordinate(value: unknown, units = "") {
  return validPoint(value)
    ? `(${value.join(", ")})${units ? ` ${units}` : ""}`
    : "Position not recorded";
}
function wireConnection(value: Record<string, unknown>) {
  return value.from && value.to
    ? `${value.from} → ${value.to}`
    : "Connection not recorded";
}
function routeDescription(value: Record<string, unknown>, units = "") {
  const points = value.points;
  if (!Array.isArray(points) || points.length < 2 || !points.every(validPoint))
    return "Route not recorded";
  const length = points
    .slice(1)
    .reduce(
      (total, point, i) =>
        total + Math.hypot(point[0] - points[i][0], point[1] - points[i][1]),
      0,
    );
  return `${points.length - 1} segments · ${Number(length.toFixed(1))}${units ? ` ${units}` : " drawing units"}`;
}
/** The native compiler retains full objects. The PM sees only the changed facts. */
export function nativeChangeValues(
  change: NativeChange,
  units = "",
): [string, string] {
  const before = nativeRecord(change.before),
    after = nativeRecord(change.after);
  if (["rewire", "add_wire", "remove_wire"].includes(change.kind))
    return [
      change.before ? wireConnection(before) : "Not present",
      change.after ? wireConnection(after) : "Not present",
    ];
  if (change.kind === "reroute")
    return [routeDescription(before, units), routeDescription(after, units)];
  if (
    ["device_geometry", "move_device", "resize_device"].includes(change.kind)
  ) {
    const attributes = [
      nativeRecord(before.attributes),
      nativeRecord(after.attributes),
    ];
    const changedAttributes = [
      ...new Set([
        ...Object.keys(attributes[0]),
        ...Object.keys(attributes[1]),
      ]),
    ].filter((key) => attributes[0][key] !== attributes[1][key]);
    const values = [before, after].map(
      (device, i) =>
        [
          JSON.stringify(before.position) !== JSON.stringify(after.position)
            ? coordinate(device.position, units)
            : "",
          before.width !== after.width || before.height !== after.height
            ? `${device.width ?? "?"} × ${device.height ?? "?"}${units ? ` ${units}` : ""}`
            : "",
          before.rotation !== after.rotation
            ? `${device.rotation ?? "?"}°`
            : "",
          ...changedAttributes.map(
            (key) =>
              `${key.replaceAll("_", " ").toLowerCase()}: ${attributes[i][key] ?? "Not recorded"}`,
          ),
        ]
          .filter(Boolean)
          .join(" · ") || "Geometry unchanged",
    );
    return [values[0], values[1]];
  }
  if (["add_device", "remove_device"].includes(change.kind)) {
    const describe = (device: Record<string, unknown>, original: unknown) => {
      if (!original) return "Not present";
      const attributes = nativeRecord(device.attributes),
        part = attributes.PART_NUMBER || device.kind;
      return [
        part && String(part).replaceAll("_", " "),
        coordinate(device.position, units),
      ]
        .filter(Boolean)
        .join(" · ");
    };
    return [describe(before, change.before), describe(after, change.after)];
  }
  return [
    nativeChangeDescription(change.before),
    nativeChangeDescription(change.after),
  ];
}
