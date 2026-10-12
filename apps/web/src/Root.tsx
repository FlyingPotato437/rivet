import { lazy, Suspense, useEffect, useState } from "react";
import { Landing, PublicHeader, PublicFooter } from "./Landing";
import { ArrowUpRight } from "@phosphor-icons/react";
import "./public-site.css";

// Workspace code and authentication are deliberately absent from the public build.
// Private development remains available for demos and acceptance testing.
const PrivateRoot = import.meta.env.DEV
  ? lazy(() => import("./PrivateRoot"))
  : null;
const PilotPage = lazy(() => import("./PilotPage"));
const workspaceRoute = () =>
  /^#(projects$|orders$|decisions$|bids$|order\/|quotes$|project\/|catalog$|activity$|settings$|guide$|shared\/)/.test(
    location.hash,
  );

export default function Root() {
  const [url, setUrl] = useState(location.pathname + location.hash);
  useEffect(() => {
    const change = () => setUrl(location.pathname + location.hash);
    window.addEventListener("hashchange", change);
    window.addEventListener("popstate", change);
    return () => {
      window.removeEventListener("hashchange", change);
      window.removeEventListener("popstate", change);
    };
  }, []);
  const pilot =
    location.pathname.replace(/\/$/, "") === "/pilot" ||
    location.hash === "#pilot";
  const privateRoute =
    workspaceRoute() ||
    /^\/(app|orders|order|projects|workspace|sign-in|sign-up|shared)(\/|$)/.test(
      location.pathname,
    );
  useEffect(() => {
    document.title = pilot
      ? "Request a pilot · Rivet"
      : privateRoute && PrivateRoot
        ? "Rivet | Workspace"
        : "Rivet | Returned drawings. Ready-to-review work.";
  }, [url, pilot, privateRoute]);
  if (privateRoute && PrivateRoot)
    return (
      <Suspense
        fallback={
          <div className="public-loading" role="status">
            Opening your workspace…
          </div>
        }
      >
        <PrivateRoot />
      </Suspense>
    );
  if (pilot)
    return (
      <Suspense
        fallback={
          <div className="public-loading" role="status">
            Opening the booking calendar…
          </div>
        }
      >
        <PilotPage />
      </Suspense>
    );
  if (privateRoute)
    return (
      <div className="rivet-public">
        <PublicHeader />
        <main className="public-closed public-wrap">
          <p className="public-eyebrow">Private pilot</p>
          <h1>
            Let’s start with
            <br />
            your workflow.
          </h1>
          <p>
            Rivet is onboarding pilot teams directly. Book a conversation to see
            the product and discuss your returned-drawing process.
          </p>
          <a className="public-button" href="/pilot">
            Request a pilot <ArrowUpRight />
          </a>
          <a className="public-text-link" href="/">
            Explore Rivet
          </a>
        </main>
        <PublicFooter />
      </div>
    );
  return <Landing />;
}
