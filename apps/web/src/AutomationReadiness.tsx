import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { ErrorNote } from "./ui";

export function AutomationReadiness() {
  const q = useQuery({
    queryKey: ["automation-readiness"],
    queryFn: () =>
      api<{ features: { name: string; status: string; detail: string }[] }>(
        "/automation-readiness",
      ),
  });
  const [show, setShow] = useState(false);
  const brief = useQuery({
    queryKey: ["daily-brief"],
    queryFn: () =>
      api<{ text: string; waiting_items_due: number }>("/daily-brief"),
    enabled: show,
  });
  return (
    <section className="settings-section automation-readiness">
      <h3>Automation</h3>
      <p className="muted">
        What is running and what still needs company setup.
      </p>
      {q.isError && <ErrorNote message="Automation status could not load." />}
      {q.data?.features.map((f) => (
        <div key={f.name}>
          <span>
            {f.name}
            <small>{f.detail}</small>
          </span>
          <strong className="automation-state">
            {f.status.replaceAll("_", " ")}
          </strong>
        </div>
      ))}
      <button className="secondary" onClick={() => setShow(!show)}>
        {show ? "Hide brief" : "Preview morning brief"}
      </button>
      {show && (
        <p role="status" className="automation-brief">
          {brief.data?.text || "Preparing brief…"}
          {brief.data
            ? ` · ${brief.data.waiting_items_due} follow-ups due`
            : ""}
        </p>
      )}
    </section>
  );
}
