import { useId, useState } from "react";
import { Check, Minus, Plus } from "@phosphor-icons/react";
import {
  nativeChangeValues,
  nativeChangeLabel,
  nativeChangeTarget,
  nativeChanges,
  nativeChecksPass,
  nativeViewport,
  type NativeChange,
  type NativeDrawing,
  type NativeVerification,
  type NativeViewport,
} from "./native-drawing-types";
import "./native-drawing-review.css";

export function NativeDrawingReview({
  verification,
  proposed = false,
}: {
  verification?: NativeVerification;
  proposed?: boolean;
}) {
  const [revision, setRevision] = useState<"before" | "after">("after"),
    [selected, setSelected] = useState(0),
    [zoom, setZoom] = useState(1);
  const id = useId();
  if (!verification) return null;
  const changes = nativeChanges(verification),
    viewport = nativeViewport(verification.before, verification.after),
    current = verification[revision],
    passing = nativeChecksPass(verification),
    attention = (verification.checks || []).filter(
      (check) => check.status !== "pass",
    ),
    passed = (verification.checks || []).filter(
      (check) => check.status === "pass",
    ),
    selectedChange = changes[selected];
  return (
    <section className="native-review" aria-labelledby={`${id}-title`}>
      <header className="native-review-heading">
        <div>
          <h4 id={`${id}-title`}>Wiring and layout</h4>
          <p>
            {proposed
              ? "Native changes Rivet plans to make."
              : "Geometry and connections read back from the saved drawing."}
          </p>
        </div>
        <span
          className={`native-check-state${passing && !proposed ? " is-checked" : ""}`}
        >
          {passing && !proposed && <Check size={14} />}
          {proposed
            ? "Proposed changes"
            : passing
              ? "Saved files rechecked"
              : "Checks need attention"}
        </span>
      </header>
      {viewport && current ? (
        <div className="native-viewer">
          <div className="native-viewer-toolbar">
            <div
              className="native-revision-switch"
              role="group"
              aria-label="Compare native drawing revisions"
            >
              <button
                type="button"
                aria-pressed={revision === "before"}
                onClick={() => setRevision("before")}
              >
                Before
              </button>
              <button
                type="button"
                aria-pressed={revision === "after"}
                onClick={() => setRevision("after")}
              >
                {proposed ? "Proposed revision" : "Checked draft"}
              </button>
            </div>
            <div className="native-zoom" role="group" aria-label="Drawing zoom">
              <button
                type="button"
                aria-label="Zoom out"
                disabled={zoom <= 1}
                onClick={() => setZoom(Math.max(1, zoom - 0.5))}
              >
                <Minus size={14} />
              </button>
              <span>{Math.round(zoom * 100)}%</span>
              <button
                type="button"
                aria-label="Zoom in"
                disabled={zoom >= 3}
                onClick={() => setZoom(Math.min(3, zoom + 0.5))}
              >
                <Plus size={14} />
              </button>
            </div>
          </div>
          <div
            className="native-canvas"
            tabIndex={0}
            aria-label="Native drawing model, scroll to inspect when zoomed"
          >
            <div className={`native-canvas-scale native-zoom-${zoom * 100}`}>
              <NativeDiagram
                key={revision}
                drawing={current}
                viewport={viewport}
                selected={selectedChange}
                label={`${revision === "before" ? "Original" : proposed ? "Proposed" : "Corrected"} native drawing model`}
              />
            </div>
          </div>
          <div className="native-viewer-caption">
            <span>
              Native model view{current.units ? ` · ${current.units}` : ""}
            </span>
            <span>
              Same scale in both revisions · full drawing in PDF below
            </span>
            {!!(current.junctions?.length || current.crossings?.length) && (
              <span className="native-connection-legend">
                Dots mark explicit junctions. Crossings without a dot are
                separate paths.
              </span>
            )}
          </div>
        </div>
      ) : (
        <p className="native-diagram-unavailable">
          The native model cannot be displayed here. Use the drawing PDF below
          to inspect the geometry.
        </p>
      )}
      {!!changes.length && (
        <div
          className="native-change-list"
          aria-label={
            proposed
              ? "Proposed native changes"
              : "Native changes made by Rivet"
          }
        >
          {changes.map((change, index) => (
            <button
              type="button"
              key={change.id || change.request_id || `${change.kind}:${index}`}
              className={selected === index ? "is-selected" : ""}
              aria-pressed={selected === index}
              onClick={() => {
                setSelected(index);
                setRevision("after");
              }}
            >
              <span className="native-change-number">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="native-change-content">
                <strong>
                  {nativeChangeTarget(change)}{" "}
                  <span>{nativeChangeLabel(change)}</span>
                </strong>
                <span className="native-change-values">
                  <span>
                    {nativeChangeValues(change, verification.after?.units)[0]}
                  </span>
                  <span className="native-change-to">to</span>
                  <span>
                    {nativeChangeValues(change, verification.after?.units)[1]}
                  </span>
                </span>
                {change.reason &&
                  change.reason !==
                    "Actual native entity/topology difference" && (
                    <small>{change.reason}</small>
                  )}
              </span>
            </button>
          ))}
        </div>
      )}
      {!!attention.length && (
        <div className="native-check-attention" role="status">
          {attention.map((check, index) => (
            <p key={check.id || index}>
              <strong>{check.label || "Native check"}</strong>
              <span>
                {check.reason ||
                  (check.status === "unknown"
                    ? "Rivet could not establish this from the supplied drawing."
                    : "The saved drawing does not pass this check.")}
              </span>
            </p>
          ))}
        </div>
      )}
      {!!passed.length && (
        <details className="native-check-details">
          <summary>
            {proposed ? "Preflight checks" : "See what was rechecked"}
          </summary>
          <ul>
            {passed.map((check, index) => (
              <li key={check.id || index}>
                <Check size={14} />
                <span>
                  <strong>{check.label || "Native check"}</strong>
                  {check.reason && <span>{check.reason}</span>}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function NativeDiagram({
  drawing,
  viewport,
  selected,
  label,
}: {
  drawing: NativeDrawing;
  viewport: NativeViewport;
  selected?: NativeChange;
  label: string;
}) {
  const title = useId(),
    grid = useId();
  const path = (points: [number, number][]) =>
    points.map((point) => viewport.point(point).join(",")).join(" ");
  return (
    <svg
      className="native-diagram"
      viewBox={`0 0 ${viewport.width} ${viewport.height}`}
      role="img"
      aria-labelledby={title}
    >
      <title id={title}>
        {label}. Device bounds, terminals and wire routes use their source
        drawing coordinates.
      </title>
      <defs>
        <pattern id={grid} width="25" height="25" patternUnits="userSpaceOnUse">
          <circle className="native-grid-dot" cx="1" cy="1" r=".8" />
        </pattern>
      </defs>
      <rect
        className="native-grid"
        width={viewport.width}
        height={viewport.height}
        fill={`url(#${grid})`}
      />
      {(drawing.enclosures || []).map((enclosure) => {
        const [x, y] = viewport.point([
          enclosure.bounds[0],
          enclosure.bounds[3],
        ]);
        return (
          <g key={enclosure.id} className="native-enclosure">
            <rect
              x={x}
              y={y}
              width={
                (enclosure.bounds[2] - enclosure.bounds[0]) * viewport.unitScale
              }
              height={
                (enclosure.bounds[3] - enclosure.bounds[1]) * viewport.unitScale
              }
            />
            <text x={x + 10} y={y + 20}>
              {enclosure.id}
            </text>
          </g>
        );
      })}
      {drawing.wires.map((wire) => {
        const active = !!selected?.wire_id && selected.wire_id === wire.id;
        const midpoint = viewport.point(
          wire.points[Math.floor(wire.points.length / 2)],
        );
        return (
          <g
            key={wire.id}
            className={`native-wire${active ? " is-selected" : ""}`}
          >
            <title>
              {wire.id}:{" "}
              {wire.actual_from === undefined
                ? wire.from || "Unresolved terminal"
                : wire.actual_from || "Unconnected"}{" "}
              to{" "}
              {wire.actual_to === undefined
                ? wire.to || "Unresolved terminal"
                : wire.actual_to || "Unconnected"}
            </title>
            <polyline points={path(wire.points)} />
            {active && (
              <text x={midpoint[0] + 8} y={midpoint[1] - 9}>
                {wire.id}
              </text>
            )}
          </g>
        );
      })}
      {(drawing.junctions || []).map((junction) => {
        const [x, y] = viewport.point(junction.position);
        return (
          <circle
            key={junction.id}
            className="native-junction"
            cx={x}
            cy={y}
            r={4}
          >
            <title>
              {junction.id}
              {junction.net_id ? ` · ${junction.net_id}` : ""}
            </title>
          </circle>
        );
      })}
      {drawing.devices.map((device) => {
        const [x, y] = viewport.point([device.bounds[0], device.bounds[3]]),
          width = (device.bounds[2] - device.bounds[0]) * viewport.unitScale,
          height = (device.bounds[3] - device.bounds[1]) * viewport.unitScale,
          active = !!selected?.device_tag && selected.device_tag === device.tag;
        return (
          <g
            key={device.tag}
            className={`native-device${active ? " is-selected" : ""}`}
          >
            <title>
              {device.tag}: {device.bounds.join(", ")}{" "}
              {drawing.units || "drawing units"}
            </title>
            <rect x={x} y={y} width={width} height={height} />
            <text
              x={x + width / 2}
              y={y + height / 2}
              textAnchor="middle"
              dominantBaseline="middle"
            >
              {device.tag}
            </text>
            {(device.terminals || []).map((terminal) => {
              const [tx, ty] = viewport.point(terminal.position);
              return (
                <g key={terminal.id} className="native-terminal">
                  <title>{terminal.id}</title>
                  <circle cx={tx} cy={ty} r={3.5} />
                  {active && (
                    <text x={tx + 7} y={ty - 7}>
                      {terminal.name || terminal.id}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}
