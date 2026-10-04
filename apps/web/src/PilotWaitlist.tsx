import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowRight, Check, CircleNotch } from "@phosphor-icons/react";
import { apiUrl } from "./api";

export function PilotWaitlist() {
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState("");
  const confirmation = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (state === "saved") {
      confirmation.current?.focus({ preventScroll: true });
      confirmation.current?.scrollIntoView({ block: "center" });
    }
  }, [state]);
  const email = import.meta.env.VITE_PILOT_EMAIL as string | undefined;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state === "saving") return;
    const data = new FormData(event.currentTarget);
    setState("saving");
    setError("");
    try {
      const response = await fetch(apiUrl("/api/pilot-requests"), {
        method: "POST",
        credentials: "omit",
        redirect: "error",
        headers: {
          "Content-Type": "application/json",
          "X-Rivet-Client": "workspace",
        },
        body: JSON.stringify({
          name: data.get("name"),
          email: data.get("email"),
          company: data.get("company"),
          workflow: data.get("workflow"),
          website: data.get("website"),
          consent: data.get("consent") === "on",
        }),
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok)
        throw new Error(
          response.status === 429
            ? "We’re receiving many requests. Please try again shortly."
            : "We couldn’t save your request. Please try again later.",
        );
      const result = await response.json();
      if (result.accepted !== true)
        throw new Error(
          "We couldn’t confirm your request. Please try again later.",
        );
      setState("saved");
    } catch (e) {
      setState("idle");
      setError(
        e instanceof Error &&
          !["TypeError", "TimeoutError", "SyntaxError"].includes(e.name)
          ? e.message
          : "We couldn’t save your request. Please try again later.",
      );
    }
  }
  return (
    <section
      className="site-pilot site-wrap"
      id="pilot"
      aria-labelledby="pilot-heading"
    >
      <div className="site-pilot-copy">
        <span className="site-kicker">
          <i /> Pilot program
        </span>
        <h2 id="pilot-heading">
          Start with one
          <br />
          <span>custom order.</span>
        </h2>
        <p>
          Help shape Rivet around the way your team works. We’re looking for
          equipment manufacturers managing customer comments, submittals, and
          revisions.
        </p>
        <ol>
          <li>
            <span>01</span> A short conversation about your workflow.
          </li>
          <li>
            <span>02</span> One redacted order and its review history.
          </li>
          <li>
            <span>03</span> A walkthrough of the record in Rivet.
          </li>
        </ol>
        <span className="site-pilot-note">
          No customer documents needed to join the waitlist.
        </span>
      </div>
      {state === "saved" ? (
        <div
          className="site-pilot-success"
          role="status"
          tabIndex={-1}
          ref={confirmation}
        >
          <span>
            <Check size={26} />
          </span>
          <h3>You’re on the pilot waitlist.</h3>
          <p>
            Your request has been saved. We’ll contact you about a pilot using
            the email you provided.
          </p>
          <a href="#preview">
            Explore the product example <ArrowRight size={16} />
          </a>
        </div>
      ) : (
        <form className="site-pilot-form" onSubmit={submit}>
          <h3>Request pilot access</h3>
          <p>Tell us a little about your team.</p>
          <div className="pilot-field-row">
            <label>
              Your name
              <input
                name="name"
                autoComplete="name"
                required
                minLength={2}
                maxLength={120}
                placeholder="Full name"
              />
            </label>
            <label>
              Company
              <input
                name="company"
                autoComplete="organization"
                required
                minLength={2}
                maxLength={180}
                placeholder="Company name"
              />
            </label>
          </div>
          <label>
            Work email
            <input
              name="email"
              type="email"
              autoComplete="email"
              required
              maxLength={254}
              placeholder="you@company.com"
            />
          </label>
          <label>
            What would you like to improve? <span>Optional</span>
            <textarea
              name="workflow"
              rows={3}
              maxLength={1200}
              placeholder="For example, tracking comments across submittal revisions."
            />
          </label>
          <label className="pilot-honeypot" aria-hidden="true">
            Website
            <input name="website" tabIndex={-1} autoComplete="off" />
          </label>
          <label className="pilot-consent">
            <input type="checkbox" name="consent" required />
            <span>
              You can contact me about the Rivet pilot. These details are used
              to evaluate and follow up on my request.
            </span>
          </label>
          {error && (
            <div className="pilot-error" role="alert">
              {error}
              {email && (
                <a href={`mailto:${email}?subject=Rivet%20pilot%20request`}>
                  Email us instead
                </a>
              )}
            </div>
          )}
          <button
            className="site-button site-button-primary"
            disabled={state === "saving"}
          >
            {state === "saving" ? (
              <>
                <CircleNotch className="is-working" size={17} /> Saving request…
              </>
            ) : (
              <>
                Join the pilot waitlist <ArrowRight size={17} />
              </>
            )}
          </button>
        </form>
      )}
    </section>
  );
}
