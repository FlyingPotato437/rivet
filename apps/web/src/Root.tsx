import { lazy, Suspense, useEffect, useState } from "react";
import { Landing } from "./Landing";

const WorkspaceApp = lazy(() => import("./App"));
const isWorkspace = () =>
  /^#(projects$|project\/|catalog$|activity$|settings$|guide$)/.test(
    location.hash,
  );

export default function Root() {
  const [workspace, setWorkspace] = useState(isWorkspace);
  useEffect(() => {
    const change = () => setWorkspace(isWorkspace());
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    document.title = workspace
      ? "Rivet | Workspace"
      : "Rivet | Every quote, connected.";
    if (workspace || !location.hash || location.hash === "#home")
      window.scrollTo(0, 0);
    else document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }, [workspace]);
  return workspace ? (
    <Suspense
      fallback={
        <div className="site-loading" role="status">
          Opening your workspace…
        </div>
      }
    >
      <WorkspaceApp />
    </Suspense>
  ) : (
    <Landing />
  );
}
