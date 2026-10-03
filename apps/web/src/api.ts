import type { components } from "./api-schema";
import { createApiRouting } from "./api-routing";
export const { apiUrl, isApiUrl } = createApiRouting(
  import.meta.env.VITE_API_URL || "", window.location.origin,
);
export type Quote = components["schemas"]["QuoteView"];
export type Line = components["schemas"]["LineView"];
export type Project = components["schemas"]["ProjectView"];
export type Workspace = components["schemas"]["WorkspaceView"];
export type Doc = components["schemas"]["DocumentView"];
export type Proposal = components["schemas"]["ProposalView"];
export type Operation = components["schemas"]["Operation-Input"];
let tokenProvider: (() => Promise<string>) | null = null;
export function setTokenProvider(provider: () => Promise<string>) {
  tokenProvider = provider;
  return () => {
    if (tokenProvider === provider) tokenProvider = null;
  };
}
export async function authorizationHeaders(): Promise<Record<string, string>> {
  const provider = tokenProvider;
  const token = await provider?.();
  if (provider !== tokenProvider)
    throw new Error("Your workspace changed. Try again.");
  return token ? { Authorization: `Bearer ${token}` } : {};
}
export async function apiFetch(path: string, init?: RequestInit) {
  const url = apiUrl(path);
  const provider = tokenProvider;
  const response = await fetch(url, {
    ...init,
    credentials: "omit",
    redirect: "error",
    headers: { ...(await authorizationHeaders()), ...init?.headers },
  });
  if (provider !== tokenProvider)
    throw new Error("Your workspace changed. Try again.");
  return response;
}
export async function api<T>(
  path: string,
  body?: unknown,
  method?: string,
): Promise<T> {
  const form = body instanceof FormData;
  const res = await apiFetch("/api" + path, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers:
      body === undefined
        ? {}
        : {
            ...(!form ? { "Content-Type": "application/json" } : {}),
            "X-Rivet-Client": "workspace",
            "Idempotency-Key": crypto.randomUUID(),
          },
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  });
  if (!res.ok) {
    let data;
    try {
      data = await res.json();
    } catch {
      throw new Error("The server could not complete this request.");
    }
    let d = data.detail;
    throw new Error(
      typeof d === "string"
        ? d
        : d?.message
          ? d.message + (d.issues ? " " + d.issues.join(" ") : "")
          : Array.isArray(d)
            ? d.map((x) => x.msg).join("; ")
            : "The request could not be completed.",
    );
  }
  return res.json();
}
export const usd = (value: string | number | null | undefined, decimals = 0) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(Number(value));
export const dateLabel = (d: string | null) =>
  d
    ? new Date(d + "T12:00:00").toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      })
    : "No deadline";
export const when = (d: string) =>
  new Date(d).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
