import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ClerkProvider,
  OrganizationList,
  OrganizationSwitcher,
  SignInButton,
  SignUpButton,
  UserButton,
  useAuth,
  useUser,
  useOrganization,
  useClerk,
  useSignIn,
} from "@clerk/react";
import { ui } from "@clerk/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Mark } from "./ui";
import { apiFetch, setTokenProvider } from "./api";
import "./auth.css";

type Account = {
  mode: "local" | "clerk";
  name: string;
  userId: string;
  role: string;
  team: string;
  demo?: boolean;
};
type DemoConfig = { email: string; user_id: string; organization_id: string };
const DemoContext = createContext<DemoConfig | null>(null);
const AccountContext = createContext<Account>({
  mode: "local",
  name: "Local PM",
  userId: "",
  role: "org:admin",
  team: "Local workspace",
});
export const useAccount = () => useContext(AccountContext);

export function Authentication({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<{
    mode: string;
    publishable_key: string;
    demo: DemoConfig | null;
  }>();
  const [error, setError] = useState("");
  useEffect(() => {
    fetch("/api/auth/config")
      .then(async (response) => {
        if (!response.ok)
          throw new Error("Rivet could not load its sign-in configuration.");
        setConfig(await response.json());
      })
      .catch((e) => setError(e.message));
  }, []);
  if (error)
    return (
      <AuthFrame title="Connection unavailable">
        <p>{error}</p>
        <button className="primary" onClick={() => location.reload()}>
          Try again
        </button>
      </AuthFrame>
    );
  if (!config)
    return (
      <div className="site-loading" role="status">
        Connecting to Rivet…
      </div>
    );
  if (config.mode === "local")
    return <SecureDownloads>{children}</SecureDownloads>;
  if (!config.publishable_key)
    return (
      <AuthFrame title="Sign-in setup is incomplete">
        <p>The server needs its Clerk application keys.</p>
      </AuthFrame>
    );
  return (
    <ClerkProvider
      ui={ui}
      publishableKey={config.publishable_key}
      afterSignOutUrl="/"
      signInFallbackRedirectUrl="/#orders"
      signUpFallbackRedirectUrl="/#orders"
      appearance={{
        variables: {
          colorPrimary: "#bca5ef",
          colorBackground: "#18191d",
          colorForeground: "#f1f0f5",
          colorMutedForeground: "#b0aebd",
          colorInput: "#24252b",
          colorInputForeground: "#f1f0f5",
          fontFamily: "Onest, sans-serif",
          borderRadius: "10px",
        },
      }}
    >
      <DemoContext.Provider value={config.demo}>
        <ClerkDownloads>
          <ClerkAccount>{children}</ClerkAccount>
        </ClerkDownloads>
      </DemoContext.Provider>
    </ClerkProvider>
  );
}

function ClerkDownloads({ children }: { children: ReactNode }) {
  const { userId, orgId } = useAuth();
  return (
    <SecureDownloads key={`${userId}:${orgId}`}>{children}</SecureDownloads>
  );
}

function SecureDownloads({ children }: { children: ReactNode }) {
  const [error, setError] = useState("");
  const [preparing, setPreparing] = useState(false);
  const [download, setDownload] = useState<{
    url: string;
    name: string;
  } | null>(null);
  const pending = useRef(false);
  const objectUrls = useRef<string[]>([]);
  useEffect(() => {
    let disposed = false;
    const handle = (event: MouseEvent) => {
      const anchor = (event.target as Element)?.closest<HTMLAnchorElement>(
        "a[href]",
      );
      if (!anchor) return;
      const url = new URL(anchor.href, location.href);
      if (url.origin !== location.origin || !url.pathname.startsWith("/api/"))
        return;
      event.preventDefault();
      if (pending.current) return;
      pending.current = true;
      setPreparing(true);
      setError("");
      void (async () => {
        try {
          const response = await apiFetch(url.pathname + url.search);
          if (!response.ok)
            throw new Error(
              (await response.json()).detail || "Unable to download this file.",
            );
          const content = await response.blob();
          if (disposed) return;
          objectUrls.current
            .splice(0)
            .forEach((url) => URL.revokeObjectURL(url));
          const blob = URL.createObjectURL(content);
          objectUrls.current.push(blob);
          const link = document.createElement("a");
          link.href = blob;
          const disposition = response.headers.get("Content-Disposition") || "";
          link.download = decodeURIComponent(
            disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1] ||
              disposition.match(/filename="([^"]+)"/)?.[1] ||
              "rivet-document",
          );
          setDownload({ url: blob, name: link.download });
          document.body.appendChild(link);
          link.click();
          link.remove();
        } catch (e) {
          setError((e as Error).message);
        } finally {
          pending.current = false;
          setPreparing(false);
        }
      })();
    };
    document.addEventListener("click", handle);
    return () => {
      disposed = true;
      document.removeEventListener("click", handle);
      objectUrls.current.forEach((url) => URL.revokeObjectURL(url));
    };
  }, []);
  return (
    <>
      {children}
      {(preparing || download) && !error && (
        <div className="auth-download" role="status">
          {preparing ? (
            <span>Preparing your file…</span>
          ) : (
            download && (
              <>
                <span>
                  <strong>File ready</strong>
                  {download.name}
                </span>
                <a href={download.url} download={download.name}>
                  Save file
                </a>
                <button
                  aria-label="Dismiss download"
                  onClick={() => {
                    URL.revokeObjectURL(download.url);
                    setDownload(null);
                  }}
                >
                  ×
                </button>
              </>
            )
          )}
        </div>
      )}
      {error && (
        <div className="toast" role="alert">
          {error}
          <button onClick={() => setError("")}>Dismiss</button>
        </div>
      )}
    </>
  );
}

function ClerkAccount({ children }: { children: ReactNode }) {
  const { userId } = useAuth();
  if (!userId)
    return (
      <AccountContext.Provider
        value={{
          mode: "clerk",
          userId: "",
          role: "",
          name: "Your account",
          team: "Choose a team",
        }}
      >
        {children}
      </AccountContext.Provider>
    );
  return <SignedInAccount>{children}</SignedInAccount>;
}

function SignedInAccount({ children }: { children: ReactNode }) {
  const { userId, orgId, orgRole } = useAuth();
  const { user } = useUser();
  const { organization } = useOrganization();
  const demo = useContext(DemoContext);
  return (
    <AccountContext.Provider
      value={{
        mode: "clerk",
        userId: userId || "",
        role: orgRole || "",
        name:
          user?.fullName ||
          user?.primaryEmailAddress?.emailAddress ||
          "Your account",
        team: organization?.name || "Choose a team",
        demo: userId === demo?.user_id && orgId === demo?.organization_id,
      }}
    >
      {children}
    </AccountContext.Provider>
  );
}

function AuthFrame({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <main className="auth-page">
      <a href="#home" className="auth-brand">
        <Mark />
        <span>rivet.</span>
      </a>
      <section className="auth-panel">
        <div className="auth-kicker">Rivet workspace</div>
        <h1>{title}</h1>
        {children}
      </section>
      <p className="auth-footnote">
        Order documents, comments, and change records.
      </p>
    </main>
  );
}

export function WorkspaceAccess({ children }: { children: ReactNode }) {
  return useAccount().mode === "local" ? (
    <>{children}</>
  ) : (
    <ClerkWorkspace>{children}</ClerkWorkspace>
  );
}

function ClerkWorkspace({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn, userId, orgId } = useAuth();
  const demo = useContext(DemoContext);
  const clerk = useClerk();
  const [teamError, setTeamError] = useState("");
  useEffect(() => {
    if (isLoaded && isSignedIn && !orgId && demo?.user_id === userId) {
      void clerk
        .setActive({ organization: demo.organization_id })
        .catch(() =>
          setTeamError("Select Rivet · public demo below to continue."),
        );
    }
  }, [isLoaded, isSignedIn, userId, orgId, demo, clerk]);
  if (!isLoaded)
    return (
      <div className="site-loading" role="status">
        Loading your account…
      </div>
    );
  if (!isSignedIn)
    return (
      <AuthFrame title="Sign in to Rivet">
        <p>Access your team’s orders and documents.</p>
        <div className="auth-actions">
          <SignInButton mode="modal">
            <button className="primary">Sign in</button>
          </SignInButton>
          <SignUpButton mode="modal">
            <button className="secondary">Create an account</button>
          </SignUpButton>
        </div>
        {demo && <DemoSignIn demo={demo} />}
        <a href="#home" className="text-button">
          Back to homepage
        </a>
      </AuthFrame>
    );
  if (!orgId)
    return (
      <AuthFrame title="Choose your team">
        <p>
          Create a workspace for your company or select a team you’ve joined.
        </p>
        <OrganizationList
          hidePersonal
          afterCreateOrganizationUrl="/#orders"
          afterSelectOrganizationUrl="/#orders"
        />
        {teamError && <p role="alert">{teamError}</p>}
        <div className="auth-account">
          <UserButton /> Manage your account
        </div>
      </AuthFrame>
    );
  return (
    <WorkspaceSession key={`${userId}:${orgId}`} orgId={orgId}>
      {children}
    </WorkspaceSession>
  );
}

function DemoSignIn({ demo }: { demo: DemoConfig }) {
  const { signIn } = useSignIn();
  const clerk = useClerk();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function enter() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      // This config is exposed only for local Clerk development instances.
      // Clerk verifies its reserved test email; backend auth is unchanged.
      for (const step of [
        () => signIn.create({ identifier: demo.email }),
        () => signIn.emailCode.sendCode(),
        () => signIn.emailCode.verifyCode({ code: "424242" }),
      ]) {
        const result = await step();
        if (result.error) throw result.error;
      }
      if (signIn.status !== "complete" || !signIn.createdSessionId)
        throw new Error("Demo sign-in needs another step. Use Sign in below.");
      await clerk.setActive({
        session: signIn.createdSessionId,
        organization: demo.organization_id,
      });
      location.hash = "orders";
    } catch (e) {
      setError((e as Error).message || "Unable to open the demo. Try again.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="auth-demo">
      <span className="auth-kicker">Try Rivet with real documents</span>
      <p>
        A separate practice workspace with public switchgear drawings and a
        scanned review. Your edits are saved.
      </p>
      <button className="primary" disabled={busy} onClick={() => void enter()}>
        {busy ? "Opening demo…" : "Try the demo"}
      </button>
      {error && (
        <p className="auth-inline-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function WorkspaceSession({
  children,
  orgId,
}: {
  children: ReactNode;
  orgId: string;
}) {
  const { getToken } = useAuth();
  const [ready, setReady] = useState(false);
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } },
      }),
  );
  useLayoutEffect(() => {
    const provider = async () => {
      const token = await getToken();
      if (!token) throw new Error("Your session expired. Sign in again.");
      const payload = JSON.parse(
        atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
      );
      if ((payload.o?.id || payload.org_id) !== orgId)
        throw new Error("Your team changed. Wait for the workspace to reload.");
      return token;
    };
    const clear = setTokenProvider(provider);
    setReady(true);
    return () => {
      clear();
      client.clear();
    };
  }, [getToken, orgId, client]);
  return ready ? (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  ) : (
    <div className="site-loading">Opening your team…</div>
  );
}

export function TeamSwitcher() {
  const account = useAccount();
  return account.mode === "clerk" ? (
    <div className="auth-team-switch">
      <OrganizationSwitcher
        hidePersonal
        afterCreateOrganizationUrl="/#orders"
        afterSelectOrganizationUrl="/#orders"
        afterLeaveOrganizationUrl="/#orders"
      />
    </div>
  ) : (
    <button
      className="workspace-switch"
      onClick={() => (location.hash = "settings")}
    >
      <span className="workspace-icon">R</span>
      <div>
        Order workspace<small>Comments and change records</small>
      </div>
    </button>
  );
}

export function AccountControl() {
  const account = useAccount();
  return account.mode === "clerk" ? (
    <div className="auth-account">
      <UserButton />
      <div>
        <strong>{account.name}</strong>
        <small>
          {account.role === "org:admin" ? "Team administrator" : "Team member"}
        </small>
      </div>
    </div>
  ) : (
    <div className="profile">
      <span>LE</span>
      <div>
        Local workspace<small>Single-user development</small>
      </div>
    </div>
  );
}

export function TeamSettings() {
  return useAccount().mode === "clerk" ? <ClerkTeamSettings /> : null;
}
function ClerkTeamSettings() {
  const clerk = useClerk();
  return (
    <section className="auth-team-settings">
      <h3>Team & members</h3>
      <p>Manage your team’s profile, membership, and invitations.</p>
      <button
        className="secondary"
        onClick={() => clerk.openOrganizationProfile()}
      >
        Manage team
      </button>
    </section>
  );
}
