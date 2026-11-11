import { lazy, Suspense, useEffect, useState } from "react";
import { Landing, PublicHeader, PublicFooter } from "./Landing";
import { LandingV2 } from "./LandingV2";
import { ArrowUpRight } from "@phosphor-icons/react";
import "./public-site.css";
import "./public-material.css";

// Workspace code and authentication are deliberately absent from the public build.
// Production workspace hosts use an explicit, separate build mode.
const workspaceBuild = import.meta.env.MODE === "workspace";
const PrivateRoot =
  import.meta.env.DEV || workspaceBuild
    ? lazy(() => import("./PrivateRoot"))
    : null;
const PilotPage = lazy(() => import("./PilotPage"));
const workspaceRoute = () =>
  /^#(today$|work$|autopilot$|projects$|orders$|review$|customers$|customer\/|decisions$|inbox$|bids$|order\/|quotes$|project\/|catalog$|activity$|settings$|guide$|shared\/)/.test(
    location.hash.split("?")[0],
  );

export default function Root() {
  const [url, setUrl] = useState(location.pathname + location.hash);
  useEffect(() => {
    if (workspaceBuild && !workspaceRoute()) {
      history.replaceState(
        null,
        "",
        `${location.pathname}${location.search}#today`,
      );
      setUrl(location.pathname + location.hash);
    }
    const change = () => setUrl(location.pathname + location.hash);
    window.addEventListener("hashchange", change);
    window.addEventListener("popstate", change);
    return () => {
      window.removeEventListener("hashchange", change);
      window.removeEventListener("popstate", change);
    };
  }, []);
  const pilot =
    !workspaceBuild &&
    (location.pathname.replace(/\/$/, "") === "/pilot" ||
      location.hash === "#pilot");
  const privateRoute =
    workspaceBuild ||
    workspaceRoute() ||
    /^\/(app|orders|order|projects|workspace|sign-in|sign-up|shared)(\/|$)/.test(
      location.pathname,
    );
  useEffect(() => {
    document.title = pilot
      ? "Request a pilot · Rivet"
      : privateRoute && PrivateRoot
        ? "Rivet | Workspace"
        : "Rivet | Customer markups in. Revised drawings out.";
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
  // The previous landing page stays reachable at /?classic for comparison.
  if (new URLSearchParams(location.search).has("classic")) return <Landing />;
  return <LandingV2 />;
}
