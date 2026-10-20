import { lazy, Suspense } from "react";
import { Authentication, WorkspaceAccess } from "./Auth";
import "./styles.css";
import "./app-refinement.css";
import "./workspace-polish.css";
const WorkspaceApp = lazy(() => import("./App"));
const SharedRecord = lazy(() =>
  import("./RecordWorkspace").then((m) => ({ default: m.SharedRecord })),
);
export default function PrivateRoot() {
  const returnTo = location.pathname + location.search + location.hash;
  return (
    <Suspense
      fallback={<div className="site-loading">Opening your workspace…</div>}
    >
      <Authentication returnTo={returnTo}>
        {location.hash.startsWith("#shared/") ? (
          <SharedRecord token={location.hash.slice(8)} />
        ) : (
          <WorkspaceAccess returnTo={returnTo}>
            <WorkspaceApp />
          </WorkspaceAccess>
        )}
      </Authentication>
    </Suspense>
  );
}
