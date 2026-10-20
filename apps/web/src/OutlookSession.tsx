import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Authentication,
  WorkspaceAccess,
  RestoredAccount,
  useAccount,
  type Account,
} from "./Auth";
import { apiUrl, authorizationHeaders, setOutlookSession } from "./api";

const returnTo = "/outlook/taskpane.html";
const headers = { "X-Rivet-Client": "workspace", "X-Rivet-Session": "outlook" };
const sameOrigin = () =>
  location.protocol === "https:" &&
  new URL(apiUrl("/api/me"), location.href).origin === location.origin;

/** Reopen with an HttpOnly, Outlook-partitioned cookie checked against live Clerk.
 * No access tokens in localStorage, query strings, or cross-window messages.
 */
export function OutlookSession({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null>();
  const [error, setError] = useState("");
  const [attempt, retry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const expired = () => setAccount(null);
    window.addEventListener("rivet-outlook-session-expired", expired);
    async function restore() {
      if (!sameOrigin()) {
        setAccount(null);
        return;
      }
      setError("");
      const response = await fetch(apiUrl("/api/outlook/session/resume"), {
        method: "POST",
        headers,
        credentials: "same-origin",
        redirect: "error",
        signal: controller.signal,
      });
      if (response.status === 401 || response.status === 403) {
        setAccount(null);
        return;
      }
      if (!response.ok)
        throw new Error(
          "Rivet could not verify your session. Please try again.",
        );
      const value: Account = await response.json();
      if (!controller.signal.aborted) setAccount(value);
    }
    void restore().catch((e) => {
      if (!controller.signal.aborted) setError(e.message);
    });
    return () => {
      controller.abort();
      window.removeEventListener("rivet-outlook-session-expired", expired);
    };
  }, [attempt]);
  if (error)
    return (
      <main className="outlook-session">
        <h1>Couldn’t reconnect</h1>
        <p role="alert">{error}</p>
        <button className="primary" onClick={() => retry((n) => n + 1)}>
          Try again
        </button>
      </main>
    );
  if (account === undefined)
    return (
      <div className="site-loading" role="status">
        Reconnecting to Rivet…
      </div>
    );
  if (account)
    return <ResumedSession account={account}>{children}</ResumedSession>;
  return (
    <Authentication returnTo={returnTo}>
      <WorkspaceAccess returnTo={returnTo}>
        <RememberSession>{children}</RememberSession>
      </WorkspaceAccess>
    </Authentication>
  );
}

function RememberSession({ children }: { children: ReactNode }) {
  const account = useAccount();
  const [error, setError] = useState("");
  const [attempt, retry] = useState(0);
  useEffect(() => {
    if (account.mode !== "clerk" || !sameOrigin()) return;
    let active = true;
    void (async () => {
      const auth = await authorizationHeaders();
      if (!active) return;
      const response = await fetch(apiUrl("/api/outlook/session"), {
        method: "POST",
        headers: { ...auth, ...headers },
        credentials: "same-origin",
        redirect: "error",
      });
      if (!response.ok)
        throw new Error(
          "Your sign-in is active, but Rivet couldn’t remember it for reopening.",
        );
      if (active) setError("");
    })().catch((e) => {
      if (active) setError(e.message);
    });
    return () => {
      active = false;
    };
  }, [account.mode, account.userId, account.team, attempt]);
  return (
    <>
      {error && (
        <div className="outlook-session-warning" role="status">
          {error} <button onClick={() => retry((n) => n + 1)}>Retry</button>
        </div>
      )}
      {children}
    </>
  );
}

function ResumedSession({
  account,
  children,
}: {
  account: Account;
  children: ReactNode;
}) {
  const [ready, setReady] = useState(false);
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: 1 } } }),
  );
  useLayoutEffect(() => {
    const clear = setOutlookSession();
    setReady(true);
    return () => {
      clear();
      client.clear();
    };
  }, [client]);
  return ready ? (
    <QueryClientProvider client={client}>
      <RestoredAccount account={account}>{children}</RestoredAccount>
    </QueryClientProvider>
  ) : null;
}
