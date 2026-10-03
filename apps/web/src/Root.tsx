import { lazy, Suspense, useEffect, useState } from "react";
import { Landing } from "./Landing";
import { WorkspaceAccess } from "./Auth";

const SharedRecord = lazy(() =>
  import("./RecordWorkspace").then((m) => ({ default: m.SharedRecord })),
);
const WorkspaceApp = lazy(() => import("./App"));
const isWorkspace = () =>
  /^#(projects$|orders$|decisions$|bids$|order\/|quotes$|project\/|catalog$|activity$|settings$|guide$)/.test(
    location.hash,
  );

export default function Root() {
  const [, setHash] = useState(location.hash);
  const [workspace, setWorkspace] = useState(isWorkspace);
  useEffect(() => {
    const change = () => {
      setWorkspace(isWorkspace());
      setHash(location.hash);
    };
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    document.title = workspace
      ? "Rivet | Workspace"
      : "Rivet | Keep every order in sync.";
    if (workspace || !location.hash || location.hash === "#home")
      window.scrollTo(0, 0);
    else document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }, [workspace]);
  if (location.hash.startsWith("#shared/"))
    return (
      <Suspense
        fallback={<div className="site-loading">Opening approved record…</div>}
      >
        <SharedRecord token={location.hash.slice(8)} />
      </Suspense>
    );
  return workspace ? (
    <Suspense
      fallback={
        <div className="site-loading" role="status">
          Opening your workspace…
        </div>
      }
    >
      <WorkspaceAccess>
        <WorkspaceApp />
      </WorkspaceAccess>
    </Suspense>
  ) : (
    <Landing />
  );
}
