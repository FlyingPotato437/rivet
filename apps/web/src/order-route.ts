const engineeringStages = new Set([
  "requirements",
  "configure",
  "drawings",
  "review",
  "release",
]);

/** Specialist engineering tools are opt-in; evidence links retain their destination. */
export function usesEngineeringWorkspace(params: URLSearchParams): boolean {
  if (
    ["approval", "source", "comment", "tab", "view", "review"].some((key) =>
      params.has(key),
    )
  )
    return false;
  return engineeringStages.has(params.get("stage") || "");
}
