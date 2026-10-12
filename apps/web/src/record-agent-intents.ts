export const DRAWING_REFERENCE_GOAL =
  "Find the drawing reference for the selected comment using the available sources. Prepare a proposed link when the evidence supports it, or draft a specific clarification request if the reference is unclear.";

export function agentRequestText(goal: string) {
  return goal
    .split("\n")
    .filter(
      (line) =>
        !line.startsWith("Context:") && !line.startsWith("Source in view:"),
    )
    .join(" ")
    .trim();
}

export function agentTaskTitle(goal: string) {
  const request = agentRequestText(goal);
  if (request !== DRAWING_REFERENCE_GOAL) return request;
  const comment = goal.match(/^Context: comment (.+?) \(comment id /m)?.[1];
  return `Check drawing reference${comment ? ` · Comment ${comment}` : ""}`;
}
