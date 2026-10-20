export type ManufacturerProfileSource = {
  document_id: string;
  name: string;
  revision: string;
  sha256: string;
  role: string;
  location: Record<string, unknown>;
};
export type ManufacturerConstraint = {
  op: string;
  field?: string;
  expected?: string | number;
  allowed?: (string | number)[];
  minimum?: number | null;
  maximum?: number | null;
  unit?: string | null;
  required_device_tag?: string;
  from_terminal?: string;
  to_terminal?: string;
  wire_id?: string;
  [key: string]: unknown;
};
export type ManufacturerRule = {
  rule_id: string;
  label: string;
  status: string;
  device_tag: string;
  source_ids: string[];
  when?: { field: string; equals: string | number; unit?: string | null }[];
  constraint: ManufacturerConstraint;
};
export type ManufacturerProfileManifest = {
  name: string;
  manufacturer_name: string;
  status: string;
  review?: { reviewer_id: string; reviewed_at: string } | null;
  sources: ManufacturerProfileSource[];
  rules: ManufacturerRule[];
  aliases: {
    alias_id: string;
    physical_device_id: string;
    tags: string[];
    status: string;
    source_ids: string[];
  }[];
};
export type ManufacturerProfileRecord = {
  id: string;
  profile_key: string;
  version_hash: string;
  manifest: ManufacturerProfileManifest;
  created_at: string;
  created_by: string;
  revocation?: {
    reason?: string;
    revoked_at?: string;
    revoked_by?: string;
    created_at?: string;
    reviewed_by?: string;
  } | null;
  source_urls?: Record<string, string>;
};

export function manufacturerProfileStatus(
  profile: ManufacturerProfileRecord,
): "proposed" | "approved" | "revoked" {
  if (profile.revocation || profile.manifest.status === "revoked")
    return "revoked";
  return profile.manifest.status === "approved" ? "approved" : "proposed";
}

const fieldLabel = (field = "field") => field.replaceAll("_", " ");
const withUnit = (value: unknown, unit?: string | null) =>
  `${String(value)}${unit ? ` ${unit}` : ""}`;

export function manufacturerRuleDescription(rule: ManufacturerRule): string {
  const c = rule.constraint;
  switch (c.op) {
    case "equals":
      return `${fieldLabel(c.field)} must be ${withUnit(c.expected, c.unit)}.`;
    case "allowed_set":
      return `${fieldLabel(c.field)} must be one of ${(c.allowed || []).map((v) => withUnit(v, c.unit)).join(", ")}.`;
    case "range":
      return `${fieldLabel(c.field)}: ${c.minimum != null ? `at least ${withUnit(c.minimum, c.unit)}` : ""}${c.minimum != null && c.maximum != null ? "; " : ""}${c.maximum != null ? `at most ${withUnit(c.maximum, c.unit)}` : ""}.`;
    case "requires":
      return `Requires device ${c.required_device_tag}. Absence needs a complete, cited inventory.`;
    case "terminal_pair":
      return `${c.wire_id}: ${c.from_terminal} ↔ ${c.to_terminal}. Checked against actual native connections.`;
    case "dimension_bounds": {
      const bounds = ["x", "y", "width", "height"].flatMap((dimension) =>
        (["min", "max"] as const).flatMap((side) => {
          const value = c[`${dimension}_${side}`];
          return value == null
            ? []
            : [
                `${dimension} ${side === "min" ? "≥" : "≤"} ${withUnit(value, c.unit || "mm")}`,
              ];
        }),
      );
      return bounds.join(" · ");
    }
    default:
      return "Unsupported rule. It cannot be enabled by this interface.";
  }
}

export function manufacturerSourceLocation(source: ManufacturerProfileSource) {
  const location = source.location || {};
  return [
    location.page != null ? `p. ${location.page}` : "",
    location.sheet ? `sheet ${location.sheet}` : "",
    location.cell
      ? `cell ${location.cell}`
      : location.row != null
        ? `row ${location.row}`
        : "",
    location.section ? `section ${location.section}` : "",
    location.cad_handle ? `CAD ${location.cad_handle}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Server-issued, protected source paths only. Never open an external URL. */
export function manufacturerSourcePath(path?: string): string | null {
  if (!path || (!path.startsWith("/api/") && !path.startsWith("/orders/")))
    return null;
  const url = new URL(
    path.startsWith("/api/") ? path : `/api${path}`,
    "https://rivet.invalid",
  );
  return url.origin === "https://rivet.invalid" &&
    url.pathname.startsWith("/api/orders/")
    ? url.pathname + url.search
    : null;
}

/** Uploads are proposals. Approval and reviewer identity come from the server. */
export function manufacturerProposal(text: string): Record<string, unknown> {
  if (text.length > 400_000)
    throw new Error("Use a profile JSON file under 400 KB.");
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error(
      "This file is not valid JSON. Export the profile again and retry.",
    );
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw))
    throw new Error("A profile must be a JSON object.");
  const input = raw as Record<string, unknown>;
  if (
    typeof input.name !== "string" ||
    !input.name.trim() ||
    !Array.isArray(input.rules) ||
    !Array.isArray(input.aliases) ||
    !Array.isArray(input.sources) ||
    !input.sources.length
  )
    throw new Error(
      "Include a profile name, source evidence, rules and aliases.",
    );
  const proposed = (items: unknown[]) =>
    items.map((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item))
        throw new Error("Each rule and alias must be an object.");
      return { ...item, status: "proposed" };
    });
  const { version: _version, review: _review, ...rest } = input;
  return {
    ...rest,
    status: "proposed",
    review: null,
    rules: proposed(input.rules),
    aliases: proposed(input.aliases),
  };
}
